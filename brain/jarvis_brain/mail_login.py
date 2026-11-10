"""Sign Jarvis in to Sir's Microsoft 365 mailbox (once, on the brain PC, as the brain's account).

    .venv\\Scripts\\python -m jarvis_brain.mail_login           # sign in
    .venv\\Scripts\\python -m jarvis_brain.mail_login --check   # who's signed in, and a test read

It shows a code: open microsoft.com/devicelogin on any device, enter it, and sign in as the
mailbox owner. Needs JARVIS_GRAPH_CLIENT_ID and JARVIS_GRAPH_TENANT_ID in .env (see graph.py).
The running brain picks up the sign-in by itself; no restart needed.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from jarvis_protocol import load_env

from .graph import Graph, GraphAuth, GraphError


async def _check(auth: GraphAuth) -> None:
    graph = Graph(auth)
    try:
        me = await graph.request("GET", "/me", {"$select": "displayName,mail"})
        inbox = await graph.request("GET", "/me/mailFolders/inbox",
                                    {"$select": "totalItemCount,unreadItemCount"})
        print(f"Signed in as {me.get('displayName')} <{me.get('mail')}>: "
              f"{inbox.get('unreadItemCount')} unread of {inbox.get('totalItemCount')} in the inbox.")
    finally:
        await graph.close()


def main() -> None:
    load_env()
    parser = argparse.ArgumentParser(prog="jarvis_brain.mail_login")
    parser.add_argument("--check", action="store_true", help="test the saved sign-in")
    args = parser.parse_args()
    auth = GraphAuth.from_env()
    try:
        if not args.check:
            print(f"Signed in as {auth.sign_in(lambda m: print(m, flush=True))}.")
        asyncio.run(_check(auth))
    except GraphError as e:
        print(e, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
