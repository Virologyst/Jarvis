# Email

Jarvis reads Sir's Microsoft 365 (Outlook) mailbox through Microsoft Graph. Built in steps:

1. **Connect and read** (done): "any new email?", "read me the one from Simon", "find the
   email about the quote". Reading one marks it read.
2. **Watch and announce** (done): the brain checks the inbox every minute, sorts new mail with
   Claude Sonnet 5.5, and Jarvis says when someone on Sir's important list writes.
3. **Draft and send** (next): replies saved as Outlook drafts; nothing is sent until Sir approves a card.

Code: `brain/jarvis_brain/graph.py` (sign-in, Graph requests), `mail.py` (inbox, read, search),
`mail_watch.py` (watching and announcing), tools `email_inbox`, `email_read`, `email_search`,
`email_important` in `tools.py` (loaded on demand).

## Announcements

- **Who:** Sir's important list, `data/email/important.json`, changed by voice: "tell me when
  Simon emails", "stop telling me about the bank", "who do you tell me about?". An entry is a
  name, an address, a domain (`@example.com`) or a description ("anyone from the bank"; the
  sorter judges those). Nobody is on it at first, so nothing is announced until Sir adds people.
- **How:** every minute (`JARVIS_EMAIL_CHECK_S`) the brain fetches inbox mail newer than the last
  it saw (`data/email/watch.json`; the first run starts from now, never the backlog). New unread
  mail goes to Claude Sonnet 5.5 (`JARVIS_EMAIL_SORT_MODEL`) in one request, which fills in a fixed
  form per email: kind (person, notification, newsletter, spam), important sender, urgent, and a
  one-line summary. Mail from the list (by the sorter, or by name, address or domain in code) is
  said on every terminal once it's quiet, like a task reminder; "read it" afterwards works.
- **Urgent mail from anyone else** is announced too only with `JARVIS_EMAIL_ANNOUNCE_URGENT=on`.
  Spam never is.
- **Quiet hours** (`JARVIS_EMAIL_QUIET`, default `22:00-07:00`, `off` for none): announcements
  wait until they end, and while no terminal is connected. Anything held over 12 hours is dropped.
- `JARVIS_EMAIL_WATCH=off` turns watching off; the email tools still work.
- **Cost:** only new unread mail is sorted, a batch per minute at most, on the small model.

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
