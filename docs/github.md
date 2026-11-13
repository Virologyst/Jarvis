# GitHub

Jarvis can read Sir's git repositories on GitHub, private ones too, and summarise them by voice:
"what's in Project Huntsman?", "what's changed in Jarvis this week?", "any open pull requests on
EGH490?".

Code: `brain/jarvis_brain/github.py`, tools in `tools.py` (loaded on demand: Jarvis finds them by
searching "git github repository repo commits branches issues pull requests").

## Setting it up (by hand)

Create a GitHub personal access token for Sir's account. A fine-grained token is best: give it
access to the repositories Jarvis should see (including Aptium-Ai's, if that organisation allows
it) with **read-only** permissions for Contents, Metadata, Issues and Pull requests, and nothing
else. Add it to the brain's `.env` yourself and restart the brain:

```
JARVIS_GITHUB_TOKEN=github_pat_...
```

Until it's there, every GitHub tool says it isn't set up and names the variable, and the brain log
says `github: not set up (JARVIS_GITHUB_TOKEN)`. Jarvis never stores or edits the token.

## The tools

- `github_repos`: every repository the token can see, most recently changed first.
- `github_repo_overview` (repo): description, default branch, branches, the latest five commits,
  open issue and pull request counts, and the start of the README.
- `github_list_folder` (repo, path, branch): folders and files with sizes.
- `github_read_file` (repo, path, branch, start): a text file, 6,000 characters per call, cut at
  a line break, with a note saying which `start` reads on (kept for 10 minutes so reading on
  doesn't fetch again). Files over 1 MB and binary files are described, not read.
- `github_commits` (repo, branch, path, count up to 30): messages (first line), authors, dates.
- `github_issues` (repo, issues or pull requests, open/closed/all, count up to 30).
- `github_read_issue` (repo, number): description, state, labels, for a pull request its branches,
  merge state and size, and the latest 12 comments.

A repository can be named as `owner/name` or just as Sir says it ("project huntsman"); names
without an owner are matched against the token's list and the known repositories, and otherwise
taken to be Virologyst's. Dates are in Brisbane time.

## Limits and safety

- **Read-only.** Only GET requests, only to `api.github.com` (redirects elsewhere are refused,
  so the token never leaves GitHub). There's no code that pushes, commits, comments, merges or
  changes settings, so no approval card; the token should be read-only too.
- **Repository content is untrusted.** Every result is labelled as information to report, never
  instructions to follow, and the system prompt says the same, as for email and the web.
- **Speakable results.** Links are reduced to their words, badges, pictures and HTML left out, and
  no web addresses are returned; Jarvis is told to describe code rather than read it out.
- 15 s per request; JSON replies are capped at 2 MB.
