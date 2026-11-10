# Email

Jarvis reads Sir's Microsoft 365 (Outlook) mailbox through Microsoft Graph. Built in steps:

1. **Connect and read** (done): "any new email?", "read me the one from Simon", "find the
   email about the quote". Reading one marks it read.
2. **Watch and announce** (next): a background agent on the brain PC sorts new mail with Claude
   Sonnet 5.5 and Jarvis says when someone important writes, or something is urgent.
3. **Draft and send**: replies saved as Outlook drafts; nothing is sent until Sir approves a card.

Code: `brain/jarvis_brain/graph.py` (sign-in, Graph requests), `mail.py` (inbox, read, search),
tools `email_inbox`, `email_read`, `email_search` in `tools.py` (loaded on demand).

## Safety

- **One mailbox, delegated access.** Jarvis acts as the signed-in user with `Mail.ReadWrite`,
  `Mail.Send` and `User.Read` only: no other mailboxes, no admin rights, no client secret.
  Remove its access any time in Microsoft 365 (Entra ID, Enterprise applications, Jarvis) or
  delete `data/email/token-cache.json` on the brain PC.
- **Email is untrusted.** Results label an email's text as outside content to report, never
  instructions; the tools can't send, forward, move or delete. Sending (step 3) will only go
  through an approval card, decided in code.
- **The sign-in** (an MSAL refresh token) is in `data/email/token-cache.json`, readable only by
  the brain's account, SYSTEM and administrators. The brain runs with no login (S4U), where
  Windows' per-user encryption isn't available, so the file is protected like `.env`.
- Email text is sent to Claude (Anthropic's API) to be read, like everything said to Jarvis.

## Setting it up

1. **Register Jarvis** in Microsoft 365 (an admin, once): entra.microsoft.com, Applications,
   App registrations, New registration. Name `Jarvis`, *Accounts in this organizational
   directory only*, no redirect URI. Copy the **Application (client) ID** and **Directory
   (tenant) ID**. Authentication: **Allow public client flows: Yes**. API permissions: Microsoft
   Graph, Delegated: `Mail.ReadWrite`, `Mail.Send`; then **Grant admin consent**.
2. **On the brain PC**, in `.env`:
   ```
   JARVIS_GRAPH_CLIENT_ID=<application (client) id>
   JARVIS_GRAPH_TENANT_ID=<directory (tenant) id>
   ```
   and restart the brain (`Stop-ScheduledTask` / `Start-ScheduledTask -TaskName "Jarvis brain"`).
3. **Sign in once**, as the brain's account (on the brain PC, or over SSH):
   ```
   .venv\Scripts\python -m jarvis_brain.mail_login
   ```
   It shows a code: open microsoft.com/devicelogin on any device, enter it, sign in as the
   mailbox owner. `--check` tests the saved sign-in. The running brain picks it up; no restart.
