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

## Limits and new work
- At most 3 normal + 1 urgent active main tasks, 10 queued (`sg task new --title ... --queue --approval "..."`,
  `sg task start <id>`), 10 ideas (`sg idea add "..."`, temporary IDEA-xxx; `sg idea promote <id> [--queue]` gives
  it a task id). Subtasks and rule-script work are not counted. A multi-step prompt: split it into this form
  (active, urgent, queued, ideas, subtasks) before working.
- Over a limit: do not take the work; tell the user to finish the waiting work first and to plan in a separate
  project (Session Guard guards session cost, not project planning).
- `sg` lists related existing work for each new task or idea: check it for overlap, conflict or a bug the new work
  could cause (finished and running tasks too) and warn the user in one line. A near copy is refused.

## Rule review (RULE REVIEW)
When Session Guard lists new or changed CLAUDE.md rule lines:
1. Conflicts: compare with the other rules (each has an id `<!-- R-001 -->`); a contradicting or repeating rule is
   updated or merged, decided with the user.
2. Scripts: could a script or hook check or do it instead of you (file names, formats, limits, line counts, commit
   messages, forbidden paths, git steps)? Look at what the project already has. Each candidate: `sg idea add
   "Script instead of a rule: <rule> -> <what the script does>" --source rule-review --rule R-xxx` (not counted
   in the idea limit). Proposals only, in one numbered list for the user.
3. A line that is state or a task, not a rule: say so; with the user's yes move it to a task and out of CLAUDE.md.
Then `sg rules reviewed` (new lines get their ids). A promoted rule-script idea becomes a rule-script task; when
the script works, `sg task done <id> --verified "<how you checked it>"` removes the rule from CLAUDE.md. Remove a
rule otherwise only with the user's yes: `sg rules remove R-xxx --approval "<their words>"`.

## STATUS and CLAUDE.md by section
Edit both by section and id (heading, TASK-ID, IDEA-ID, R-ID), never by line number; delete a line completely.
Session Guard removes empty lines and empty bullets an edit leaves behind. A bullet under STATUS "## Notes" becomes
an idea with an id.

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
