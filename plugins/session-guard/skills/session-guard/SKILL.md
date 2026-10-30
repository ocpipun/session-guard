---
name: session-guard
description: Session Guard procedures - hand-off note, break hand-off and "finish session". Use when a "[Session Guard]" instruction says HAND-OFF, BREAK RULE, FINISH SESSION, TARGET STOP, NO WORK or 700k, when the user writes "finish session", or when the user asks for a hand-off note.
---

# Session Guard

Numbers come from the "[Session Guard]" line on each prompt; never estimate them. No line means "no measurement".
The rules themselves are given at session start by the plugin.

## Hand-off (300k, 700k, 800k, or on request)
1. Read `docs/STATUS.md` (create `docs/` and the file if missing). If the project's CLAUDE.md sets its own rules
   for STATUS, follow them.
2. Remove finished items and needless detail. If a line will matter later (decision, measurement, proven cause,
   how to undo something), move it as one dated line to `docs/STATUS_ARCHIVE.md` under the matching heading;
   otherwise delete it. Do not read the whole archive; find the heading by search.
3. Write a "## Hand-off note" section in `docs/STATUS.md` (update it if present; never a second one), in the
   user's language:
   - Date, and context from the Session Guard line
   - Current goal
   - Done: one line
   - Half-done: file / step, where it stopped
   - Next step: the first thing the new session does
   - Pending user command: word for word, if any
   - Running jobs and how to check them, if any
4. Tell the user in one line that the hand-off note is ready.

## Break hand-off (Session Guard: BREAK RULE)
- Do not run the user's command; no reading or research for it.
- Only do "Hand-off"; copy the user's message into "Pending user command" word for word.
- Tell the user: hand-off note ready, continue in a new session. Session Guard locks this session.

## Finish session ("finish session", any mode)
- Do nothing else the message asks.
1. "Hand-off".
2. Measurement: run the `--report` command given in the Session Guard instruction. If the app has a usage tool,
   also read the weekly and 5-hour quota percentages. If `docs/STATUS_ARCHIVE.md` has a "Session measurements"
   table, add one row; a value you cannot read is written as "n/a", never invented.
3. Tell the user: session closed, open a new session for the next topic. Session Guard locks this session.
