"""Microsoft Graph: Sir's Microsoft 365 mailbox and calendar, signed in once on the brain PC.

Jarvis is registered in V Source's Microsoft 365 (Entra ID) as a public client with delegated
permissions for the signed-in user's own mail (Mail.ReadWrite, Mail.Send) and, read-only, his own
calendar (Calendars.Read), and nothing else: no other mailboxes, no admin rights, no client
secret. Email asks only for the mail scopes, so a sign-in from before the calendar was added
keeps email working; the calendar asks for its own scope and says so if Sir needs to sign in
again to give it. Sir signs in once with a device code
(`python -m jarvis_brain.mail_login`); MSAL keeps a refresh token in data/email/token-cache.json
and renews access tokens itself.

The brain runs as a scheduled task with no login (S4U), where Windows' per-user encryption
(DPAPI) isn't available, so the cache is a plain file that only the brain's account (and
administrators) may read, like the API key in .env. Delete it, or remove Jarvis's access in
Microsoft 365, to sign Jarvis out.

Settings (.env): JARVIS_GRAPH_CLIENT_ID and JARVIS_GRAPH_TENANT_ID, from the app registration.
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path

import aiohttp

from .config import REPO_ROOT

log = logging.getLogger(__name__)

GRAPH = "https://graph.microsoft.com/v1.0"
# offline_access (for the refresh token) is added by MSAL itself
SCOPES = ["Mail.ReadWrite", "Mail.Send", "User.Read"]
CALENDAR_SCOPES = ["Calendars.Read"]        # read-only: Jarvis never changes the calendar
TOKEN_CACHE = REPO_ROOT / "data" / "email" / "token-cache.json"
MAX_RETRIES = 3


class GraphError(RuntimeError):
    """Something to tell the person: not set up, not signed in, Microsoft said no."""


class GraphAuth:
    """Access tokens for the signed-in user, from MSAL's cache."""

    def __init__(self, client_id: str | None, tenant_id: str | None,
                 cache_path: Path = TOKEN_CACHE):
        self.client_id = (client_id or "").strip()
        self.tenant_id = (tenant_id or "").strip()
        self.cache_path = cache_path
        self._app = None
        self._cache = None
        self._lock = asyncio.Lock()

    @classmethod
    def from_env(cls) -> "GraphAuth":
        return cls(os.environ.get("JARVIS_GRAPH_CLIENT_ID"), os.environ.get("JARVIS_GRAPH_TENANT_ID"))

    @property
    def configured(self) -> bool:
        return bool(self.client_id and self.tenant_id)

    def _application(self):
        if not self.configured:
            raise GraphError("Email isn't set up yet: JARVIS_GRAPH_CLIENT_ID and "
                             "JARVIS_GRAPH_TENANT_ID need adding to the brain's .env.")
        if self._app is None:
            import msal   # only needed once email is set up
            self._cache = msal.SerializableTokenCache()
            if self.cache_path.exists():
                self._cache.deserialize(self.cache_path.read_text(encoding="utf-8"))
            self._app = msal.PublicClientApplication(
                self.client_id, authority=f"https://login.microsoftonline.com/{self.tenant_id}",
                token_cache=self._cache)
        return self._app

    def _save(self) -> None:
        if self._cache is None or not self._cache.has_state_changed:
            return
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.cache_path.with_name(self.cache_path.name + ".tmp")
        tmp.write_text(self._cache.serialize(), encoding="utf-8")
        restrict_to_owner(tmp)
        os.replace(tmp, self.cache_path)
        self._cache.has_state_changed = False

    def account(self) -> str | None:
        """Who's signed in (their email address), or None."""
        accounts = self._application().get_accounts()
        return accounts[0].get("username") if accounts else None

    def _token_now(self, scopes: list[str]) -> str:
        app = self._application()
        accounts = app.get_accounts()
        if not accounts:
            raise GraphError("Email isn't signed in yet: run the sign-in on the brain PC "
                             "(python -m jarvis_brain.mail_login).")
        result = app.acquire_token_silent(scopes, account=accounts[0])
        self._save()
        if not result or "access_token" not in result:
            reason = (result or {}).get("error_description", "no saved sign-in")
            log.warning("graph: silent sign-in failed for %s: %s", scopes, reason)
            if scopes == CALENDAR_SCOPES:
                raise GraphError("Jarvis can't see the Outlook calendar yet: it needs calendar "
                                 "permission (Calendars.Read) in Microsoft 365 and Sir signing "
                                 "in again (python -m jarvis_brain.mail_login on the brain PC).")
            raise GraphError("Microsoft needs Sir to sign Jarvis in to email again "
                             "(python -m jarvis_brain.mail_login on the brain PC).")
        return result["access_token"]

    async def token(self, scopes: list[str] | None = None) -> str:
        async with self._lock:   # MSAL isn't async; one refresh at a time
            return await asyncio.to_thread(self._token_now, scopes or SCOPES)

    # Signing in (the mail_login script)

    def sign_in(self, show: Callable[[str], None] = print) -> str:
        """Device-code sign-in: shows a code to enter at microsoft.com/devicelogin, waits."""
        app = self._application()
        flow = app.initiate_device_flow(scopes=SCOPES + CALENDAR_SCOPES)
        if "user_code" not in flow:
            raise GraphError(f"Microsoft wouldn't start a sign-in: "
                             f"{flow.get('error_description') or flow}")
        show(flow["message"])
        result = app.acquire_token_by_device_flow(flow)   # waits until done or expired
        if "access_token" not in result:
            raise GraphError(f"Sign-in didn't finish: {result.get('error_description') or result}")
        self._save()
        return (result.get("id_token_claims") or {}).get("preferred_username") or "signed in"


def restrict_to_owner(path: Path) -> None:
    """Only this account, SYSTEM and administrators may read the file."""
    if sys.platform != "win32":
        os.chmod(path, 0o600)
        return
    user = os.environ.get("USERNAME", "")
    try:
        subprocess.run(["icacls", str(path), "/inheritance:r", "/grant:r", f"{user}:F",
                        "/grant:r", "SYSTEM:F", "/grant:r", "*S-1-5-32-544:F"],
                       check=True, capture_output=True)
    except (OSError, subprocess.CalledProcessError) as e:
        log.warning("couldn't restrict %s to its owner: %s", path, e)


Fetch = Callable[[str, str, dict, dict | None], Awaitable[tuple[int, dict, dict]]]


class Graph:
    """Calls to Microsoft Graph as the signed-in user, retrying throttling and expired tokens."""

    def __init__(self, auth: GraphAuth, fetch: Fetch | None = None):
        self.auth = auth
        self._fetch = fetch or self._http
        self._session: aiohttp.ClientSession | None = None

    async def _http(self, method: str, url: str, headers: dict,
                    body: dict | None) -> tuple[int, dict, dict]:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30))
        async with self._session.request(method, url, headers=headers, json=body) as r:
            data = await r.json(content_type=None) if r.content_length != 0 else {}
            return r.status, dict(r.headers), data or {}

    async def request(self, method: str, path: str, params: dict | None = None,
                      body: dict | None = None, headers: dict | None = None,
                      scopes: list[str] | None = None, what: str = "email") -> dict:
        """`scopes` other than the mail ones (the calendar's); `what` names the service in
        what Jarvis says if it fails."""
        url = path if path.startswith("http") else GRAPH + path
        if params:
            url += ("&" if "?" in url else "?") + "&".join(
                f"{k}={_quote(str(v))}" for k, v in params.items())
        for attempt in range(MAX_RETRIES + 1):
            token = await (self.auth.token(scopes) if scopes else self.auth.token())
            status, reply_headers, data = await self._fetch(
                method, url, {"Authorization": f"Bearer {token}", **(headers or {})}, body)
            if status in (429, 503, 504) and attempt < MAX_RETRIES:
                wait = float(reply_headers.get("Retry-After") or 2 ** attempt)
                log.info("graph: %s, waiting %.0f s", status, wait)
                await asyncio.sleep(min(wait, 30))
                continue
            if status >= 400:
                message = (data.get("error") or {}).get("message") or f"HTTP {status}"
                log.warning("graph %s %s: %s %s", method, path, status, message)
                if status in (401, 403):
                    raise GraphError(f"Microsoft refused Jarvis's access to {what} "
                                     f"({message}). It may need signing in again.")
                if status == 404:
                    raise GraphError("That email isn't there any more." if what == "email"
                                     else f"Microsoft couldn't find that in the {what}.")
                raise GraphError(f"Microsoft's {what} service said: {message}")
            return data
        raise GraphError(f"Microsoft's {what} service is busy; try again in a minute.")

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()


def _quote(value: str) -> str:
    from urllib.parse import quote
    return quote(value, safe="'$,=:()/")
