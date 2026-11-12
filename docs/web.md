# The internet

Jarvis can look things up online: "find me a multi-bay hard drive dock and what it costs",
"what's the news on ...", "when does Bunnings close today?".

Code: `brain/jarvis_brain/web.py`, tools `web_search` and `read_web_page` in `tools.py` (loaded
on demand: Jarvis finds them by searching "web search internet browse look up online").

## Setting it up (by hand)

Web search uses the [Brave Search API](https://brave.com/search/api/). Get a key there (the free
plan is enough for office use), then add it to the brain's `.env` yourself and restart the brain:

```
JARVIS_BRAVE_API_KEY=your-key-here
JARVIS_WEB_COUNTRY=AU        # optional: whose results and prices come first (default AU)
```

Until the key is there, `web_search` says it isn't set up and names the variable, and the brain
log says `web search: not set up (JARVIS_BRAVE_API_KEY)`. Reading a page by its address needs
no key.

## The tools

- `web_search` (query, count 1 to 10, default 5): the top results, each with title, a short
  snippet, the site it's from, how old it is when Brave knows, and the address (for
  `read_web_page`; Jarvis is told never to read addresses out). 10 s limit.
- `read_web_page` (url, start): fetches the page and returns its main readable text: scripts,
  styles, menus, headers, footers, sidebars, cookie banners and adverts left out, and just the
  `<main>` or `<article>` part when the page marks one. Up to 6,000 characters per call, cut at
  a paragraph or sentence, with a note saying which `start` reads on; the page is kept for 15
  minutes (8 pages at most) so reading on doesn't fetch it again. Plain text pages are read as
  they are; PDFs and other files are described, not read.

## Limits and safety

- **Read-only.** Only GET requests, no cookies, no logins (addresses with a username or password
  are refused). Jarvis can't buy, sign up or fill in forms, so there's no approval card.
- **Only the public internet.** Addresses on the office network, the brain itself, link-local
  and cloud-metadata addresses, `localhost` and `.local`/`.lan` names are refused. Names are
  checked when they're looked up (every address must be public), and redirects are followed by
  hand, at most 5, each one checked again, so a page can't bounce Jarvis to a device in the office.
- **Timeouts and sizes.** A page has 15 s in total (8 s to connect) and at most 3 MB is
  downloaded (the result says when a page was cut short); at most 200,000 characters of text are
  kept from one page. Search replies are capped at 1 MB.
- **Web content is untrusted.** Every result is labelled as outside content to report on, never
  instructions to follow, and the system prompt says the same, as for email.
- **What leaves the office:** search queries go to Brave with the key; reading a page contacts
  that site. Nothing else is sent; `logs\brain.log` records each tool call and any errors.

## How Jarvis answers

The system prompt asks for a short spoken summary: the answer first, then a couple of options or
key details, prices said naturally, always which site it came from, a caution when prices may
differ or be out of date, and never web addresses read out.
