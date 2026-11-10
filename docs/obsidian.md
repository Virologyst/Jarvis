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
prepend, replace one section, create a note), `obsidian_show`, `obsidian_search`,
`obsidian_canvas`. They need no approval card (they're Sir's own notes) but never replace a
whole note or overwrite an existing one, and Jarvis asks before reorganising anything.

`obsidian_canvas` also keeps a board up to date: it changes a card's text, colour, position or
size, changes or removes a link's label, and removes cards (with their arrows) and links. Cards
are named by id, exact text or a part of the text only one card has; links by the cards they
join (or id). Everything is found before anything changes, so a missing or ambiguous card
changes nothing and the candidates come back. Every other card, arrow and field in the board
(groups, file and link cards) is kept as it was. Removals go on an approval card: the terminal
first checks the change without saving (`preview`) so the card says exactly what will go.
Obsidian's file recovery (Settings, Core plugins) keeps snapshots if an edit needs undoing.

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
