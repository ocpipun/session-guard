---
name: session-guard
description: Session Guard 2.0 procedures - task work, hand-off, break hand-off and "finish session". Use when a "[Session Guard]" instruction says HAND-OFF, BREAK RULE, FINISH SESSION, TARGET STOP or NO WORK, when the user writes "finish session", or when the user asks for a hand-off.
---
<!-- SESSION-GUARD: 2.0 | component: skill-session-guard | version: 2.0.0 -->

# Session Guard 2.0

`sg` = the command given at session start (`python "<plugin>/scripts/session_guard.py"`). Your SESSION-ID is in
`$SG_SESSION_ID` (else pass `--session <id>`). Numbers come only from "[Session Guard]" lines; never estimate.

## Task work
- `sg task list` · `sg task show <id>` · `sg task claim <id>` (a task another active session owns: ask the user;
  with their yes `--takeover "<their words>"`).
- New main task only with the user's approval: `sg task new --title "..." --approval "<their words>"`; subtask:
  `--parent <id>` (no approval). Unrelated work: `sg idea add "..."`.
- After each step: `sg task note <id> --done "..." --pending "..."`; finished: `sg task done <id>` at once.
- Redo or extend a finished task: `sg task reopen <id> --title "..." --relation reopens|extends --approval "..."`.
- Never edit the task block of docs/STATUS.md or `.claude/session-guard2/`; free notes go under "## Notes".

## Hand-off (300k, 800k, finish, break, or on request)
Only on a Session Guard instruction or the user's request (250k is a notice only).
1. Make the work hand-off ready: finish the small step in progress so nothing is half-written (a file that does
   not run, an edit cut in the middle); start nothing new.
2. For each open task you own (the instruction lists them): `sg handoff write <id> --technical "<files,
   commands, where it stopped, what to check first>"` (optional `--done`, `--pending`, `--pr <url>`). It also
   records where the work is:
   - Cloud: it commits the task's changed files for the task's side branch sg/<task-id>. Owning several tasks:
     add `--paths <files or folders of this task>` (`--no-files` when it changed none).
   - Computer: the work stays in this folder; the uncommitted files are listed. A side branch only with the
     user's yes (`--side-branch`): propose it when the work is large or risky to leave uncommitted.
   A finished task needs none; an unchanged hand-off is not written again.
3. `sg handoff publish`. Cloud / side branch: pushes each task's branch and verifies it; "NOT VERIFIED" means the
   hand-off is not done (tell the user why in one line); unsaved files are refused (save them first). Computer:
   the hand-off stays in the folder.
4. Tell the user in one line: hand-off ready (task ids, and in the cloud each side branch), continue in a new
   session. When a new session claims the task, this session is locked.
5. 300k (normal) or 800k (long mode) hand-off: also the SESSION MEASUREMENT the instruction gives, as in "Finish
   session" step 2 ("finish session" is refused after this hand-off).

## Taking over
`sg task show <id>` first: the "Work:" line says where the work is. A task on a side branch this checkout lacks
(session start lists "HAND-OFF BRANCHES"): `sg handoff adopt sg/task-...` merges its work and hand-off here
(conflicts in your own files stop it and change nothing); then `sg task claim <id>`. Ask the user when several
can be taken.

## Break hand-off (BREAK RULE)
Do not run the user's command; no reading or research for it. Session Guard already stored it word for word in
each task you own. Do "Hand-off", tell the user to continue in a new session.

## Finish session
Do nothing else the message asks. 1. "Hand-off". 2. Run the `report` command from the instruction; read the
weekly and 5-hour quota with the usage tool if there is one; add one row to "Session measurements" in
docs/STATUS_ARCHIVE.md ("n/a" for a value you cannot read; never invent one). 3. Tell the user: session closed.
