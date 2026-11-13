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

## Scheduled briefings

`brain/jarvis_brain/briefings.py`: Jarvis says two briefings on his own, like reminders (queued
on every screen, said once each is quiet, then the mic stays open for "snooze it", "I've mowed
the lawns" or "what else?"):

- **Weekday priorities**, Monday to Friday at 9 am Brisbane: the same gathering and ranking as
  `daily_priorities`, said as how many things there are and the top four in order.
- **Personal briefing**, Saturdays at 9 am: from `Projects/Me` only (no work notes, calendar or
  to-do list): routines that are due, dated tasks, then every other open task under its heading
  (personal projects and to-dos). Purchases and the Log stay out.

The words are put together in code; the whole list goes into the conversation so Jarvis can give
the rest. Times, days, on/off, a pause until a day and a snooze are kept in `data/briefings.json`
and changed by voice with `briefing_schedule` ("move the Saturday briefing to eight", "turn the
weekday briefing off", "pause it until Monday", "snooze it ten minutes"; also "now" to hear one).
One that fell due while the brain was off or no screen was connected is said up to two hours
late, otherwise it waits for its next day; a new time that's already gone today starts next time.
That file is Jarvis's own settings, like the theme, so changing it needs no approval card.
