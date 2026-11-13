# Daily priorities

"What are my priorities for today?", "what's on today?", a morning briefing: Jarvis answers with
one ranked list from three places, says how many things there are and the top three or four, and
offers the rest.

Code: `brain/jarvis_brain/priorities.py` (ranking), `outlook_calendar.py` (the calendar),
`jarvis_protocol/routines.py` (routines, shared with the terminal), tool `daily_priorities` in
`tools.py` (loaded on demand, with a date and a person, both optional).

## Where it looks

1. **Outlook calendar** (read-only, see [email.md](email.md#calendar-read-only)): the day's
   events, Brisbane time.
2. **The to-do list** (`tasks.py`): for the person asking (Craig by default), tasks due that day,
   overdue ones (when asking about today), and on-hold ones, listed apart. Someone else's
   priorities are their to-do list only; the calendar and notes are Sir's.
3. **Obsidian** (every note in `Projects/`): live through the terminal on Sir's PC
   (`obsidian_project_notes`, read-only), or from the vault on the brain when Obsidian isn't
   reachable. From each note: open tasks with a date (`📅 2026-11-13`, `⏳`, `due:: 2026-11-13`,
   `due 2026-11-13`, optionally with a time after it, `📅 2026-11-13 14:00`), and from active
   projects their `next` step and open `#j` tasks (most recently updated projects first). Undated
   tasks are counted, not listed. Templates, done projects, the `Log` and Purchases or shopping
   lists are left out.

If a source can't be reached, the rest is still given and Jarvis says which one was missing.

## The order

1. Fixed times: calendar events (all-day ones first) and timed tasks, in time order
2. Overdue tasks, oldest first
3. Tasks due that day
4. Daily routines not yet done that day
5. Weekly and monthly routines that are due, longest waiting first
6. Project next steps (top five), then `#j` tasks (top five)

The same thing in two places (a task in the to-do list and the notes, a meeting that's also a
task) is said once, at its higher place, keeping the calendar's details and the task's number:
two items are one when they have the same words, or one's words (at least two) make up most of
the other's.

## Routines

Under a `Routines` heading in `Projects/Me`, Daily, Weekly and Monthly lists (subheadings, bold or
plain labels, or bullets with the routines nested under them):

```markdown
## Routines
### Daily
- Clean the kitchen
### Weekly
- Mow the lawns (last done 2026-11-08)
- Clean the car
### Monthly
- Clean the caravan
- Plant the deck garden
- Sort the weeds
```

"I've mowed the lawns" makes Jarvis call `routine_done`, which writes `(last done <today>)` at the
end of that line (and ticks it, if it's a checkbox): the only thing the daily priorities ever
write. A daily routine is due again the next day, a weekly one seven days after it was done, and a
monthly one a calendar month after; one never done is due. A box ticked by hand with no date
counts as done, and Jarvis mentions it so the date can be recorded. Through Obsidian the change is
saved only if Sir hasn't typed since it was read; on the brain the file is replaced in one step.
It isn't written in a Log: the date is the record.
