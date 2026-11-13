# Working in Obsidian with Jarvis

Jarvis works in Sir's Obsidian live, on his screen: sees which note is open, writes under a
heading as Sir watches (a new bullet is a new branch on the note's mind map), opens notes, and
adds cards and arrows to canvas boards. Good for brainstorming out loud: "J, let's mind map the
launch", "add hosting costs under pricing", "put a card for the Pi next to the brain on the
board".

## How it works

Obsidian runs on Sir's PC; the brain runs on the brain PC. So the terminal on Sir's PC carries
Jarvis's requests to Obsidian's **Local REST API** plugin there, on 127.0.0.1 only
(`terminal/jarvis_terminal/obsidian.py`). Going through Obsidian, not the files, means edits land
in the note even while Sir has it open.

Tools (loaded on demand): `obsidian_open_note`, `obsidian_read`, `obsidian_write` (append,
prepend, replace one section, create a note), `obsidian_delete`, `obsidian_show`,
`obsidian_search`, `obsidian_canvas`. They need no approval card (they're Sir's own notes) but
never replace a whole note or overwrite an existing one, and Jarvis asks before reorganising
anything.

Sections are found by the terminal itself (`jarvis_protocol/markdown.py`, shared with the
`vault_*` tools), not the plugin: lines starting with `#` inside fenced code blocks (``` or
`~~~`, such as a `markmap` block) or the front matter aren't headings. A section runs from its
heading to the next heading of the same or a higher level. `replace` swaps that section's text in
place, keeping the heading line as it was, and changes nothing if the heading isn't there;
`append` adds at the end of the section (a missing heading is made: `##` at the end of the note,
or one level under the deepest parent that's there). Every edit is saved with If-Match.

`obsidian_delete` removes an exact passage, or a heading with its whole section (a `Log` heading
under it is left alone), only when Sir asks. It goes on an approval card: the terminal first
finds it without saving (`preview`), so a passage that's missing or there more than once is said
straight away with the copies (`which`, 1 = first in the note, picks one). Once approved, the
brain sends the same request with that preview as `expect`; the terminal deletes only if the
note still gives exactly the same, and saves only if Sir hasn't typed since it read (If-Match).

`obsidian_canvas` also keeps a board up to date: it changes a card's text, colour, position or
size, changes or removes a link's label, and removes cards (with their arrows) and links. Cards
are named by id, exact text or a part of the text only one card has; links by the cards they
join (or id). Everything is found before anything changes, so a missing or ambiguous card
changes nothing and the candidates come back. Every other card, arrow and field in the board
(groups, file and link cards) is kept as it was. Removals go on an approval card: the terminal
first checks the change without saving (`preview`) so the card says exactly what will go.
Obsidian's file recovery (Settings, Core plugins) keeps snapshots if an edit needs undoing.

Two more ops serve the daily priorities ([priorities.md](priorities.md)):
`obsidian_project_notes` reads every note in `Projects/` (read-only) and `obsidian_routine_done`
writes a routine's "last done" date in `Projects/Me`, saved only if Sir hasn't typed since.

It works while Obsidian is open on that PC and its terminal is running. The `vault_*` tools
(docs/vault.md) are the other route: files on the brain PC, for when Obsidian isn't open.

## Setting it up (on Sir's PC)

1. Obsidian: Settings, Community plugins, Browse, **Local REST API** (by Adam Coddington),
   Install, Enable.
2. Its settings page shows an **API key**. Put it in this PC's `.env` (never committed):
   ```
   JARVIS_OBSIDIAN_KEY=<the key>
   ```
   The default address `https://127.0.0.1:27124` is right unless the port was changed
   (`JARVIS_OBSIDIAN_URL`).
3. Restart the terminal. Its log says "Jarvis can work in Obsidian here", and the brain's
   says the terminal connected "(with Obsidian)".
