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
- After each step: `sg task note <id> --done "..." --pending "..."`; finished: `sg task done <id> --summary
  "<result in one plain sentence>"` at once (cancel: why). The line goes to docs/COMPLETED_TASKS.md.
- Your last task finished: write the project state (Session Guard says so): read docs/COMPLETED_TASKS.md only, then
  `sg handoff note --project-state --text "<where the project stands and what is left, 2-3 sentences>"`.
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
3. ONE session note: `sg handoff note --text "<overall assessment in 2-3 sentences: what this session did, the
   state it leaves, what the next agent should look at first>"`. Session Guard adds your signature, the date and
   the quota readings; it stands under "Hand-off notes" in STATUS (several agents: several notes). Once every task
   it hands over is taken and a newer note is written, it goes to the archive as one line.
4. `sg handoff publish` (refused without the note). Cloud / side branch: pushes each task's branch and verifies it; "NOT VERIFIED" means the
   hand-off is not done (tell the user why in one line); unsaved files are refused (save them first). Computer:
   the hand-off stays in the folder.
5. Tell the user in one line: hand-off ready (task ids, and in the cloud each side branch), continue in a new
   session. When a new session claims the task, this session is locked.
6. 300k (normal) or 800k (long mode) hand-off: also the SESSION MEASUREMENT the instruction gives, as in "Finish
   session" step 2 ("finish session" is refused after this hand-off).

## Limits and new work
- At most 10 normal and 3 urgent active main tasks per agent working in the project at the same time (+10 and
  +3 for each further agent), 50 queued (`sg task new --title ... --queue --approval "..."`, `sg task start <id>`),
  50 ideas (`sg idea add "..."`, temporary IDEA-xxx; `sg idea promote <id> [--queue]` gives it a task id).
  Subtasks and rule-script work are not counted. A multi-step project or a bulk prompt: split it into this form
  (active, urgent, queued, ideas, subtasks) before working.
- Urgent (`--kind urgent --reason "..." --approval "..."`): listed first and belongs to its agent; while it is
  active its owner takes no new task (new, queued, started or promoted): tell the user, record new requests as
  ideas. Other agents go on.
- Subagents: only a subtask of a task you own, opened first (`sg task new --parent <id>`) and named in the
  prompt; never a new, independent task. Subagents and subtasks are not counted.
- Child session agents (a new session started from yours): its brief carries `SG-PARENT: <your SESSION-ID> bound`
  (it helps with your work, only subtasks of your tasks; id <your id>-B<n>) or `... independent` (its own main
  tasks with the user's approval words, never your work; id <your id>-I<n>). The kinds never take each other's
  work; a session that takes over your hand-off is a continuation, not a child.
- Your hand-off with bound children: FIRST `sg handoff children`, send each NOT READY child the message it prints,
  run it again until all are ready, only then write your hand-offs. A child that cannot answer: ask the user, then
  `--children-gone "<the user's words>"`.
- In a child: "PARENT SESSION ... HANDS OFF" (bound): stop, write the hand-off of every task you own, then
  `sg handoff publish`; the session is locked after it. "PARENT SESSION ... STOPPED" (independent): per task you
  own, finish the small step in progress, then `sg handoff write <id> --technical "..."` (branch and PR are
  recorded; add `--pr <link>` if missing), then go on.
- Over a limit: do not take that item; tell the user to finish or drop waiting work first.
- New work (a bulk prompt at project start, or new tasks later), before taking any of it: ONE `sg task check
  --title "<item 1>" --title "<item 2>" ...`. It shows the limits, every running, urgent, queued task and idea,
  finished work sharing words with the new items (pre-2.0 items found by keywords) and near copies. Compare them
  yourself; a near copy, overlap, conflict or contradiction: explain it plainly to the user in their language and
  let them decide. A near copy is refused. Read no other file for this, not the archive.
- Queued tasks live in docs/TASK_BACKLOG.md, ideas in docs/IDEA_TASKS.md: both generated, one section per id, a
  hand edit is undone. A bullet written under the "## Add an idea" heading of IDEA_TASKS.md, or under a
  "## Notes" heading of STATUS, becomes an idea with an id.
- The archive: only when needed, by search: 2.0 entries by id (TASK-xxx, IDEA-xxx, R-xxx, S-...), pre-2.0
  entries by keywords.
- After you merge a pull request or a side branch: tell the user its side branch can be deleted on GitHub (it is
  the project's branch, not Session Guard's), and that "Automatically delete head branches" does it by itself.

## Rule review (RULE REVIEW)
When Session Guard lists new or changed CLAUDE.md rule lines:
0. Session Guard first (user decision): a line that contradicts or overlaps a Session Guard rule is removed now, no
   question: `sg rules reviewed`, then `sg rules remove R-xxx --conflict "<the Session Guard rule>"`; tell the user
   in one line which rule went and why. A rule the user asks to add that would contradict Session Guard is not
   added: say which Session Guard rule it breaks. Rules in other instruction files (CLAUDE.local.md,
   .claude/rules/, AGENTS.md, the global CLAUDE.md outside Session Guard's block): delete the line the same way.
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
Session Guard removes empty lines and empty bullets an edit leaves behind.

## Taking over
`sg task show <id>` first: the "Work:" line says where the work is. A task on a side branch this checkout lacks
(session start lists "HAND-OFF BRANCHES"): `sg handoff adopt sg/task-...` merges its work and hand-off here
(conflicts in your own files stop it and change nothing); then `sg task claim <id>`. The "Agents" line under a
task says who handed it over and when; that session's note under "Hand-off notes" is its overall assessment.
Session start: propose finishing work handed off first (several: recommend one, the user chooses); only with none
waiting and new work wanted the backlog, then the ideas (design by questions, promote to the queue, start).

## Break hand-off (BREAK RULE)
Do not run the user's command; no reading or research for it. Session Guard already stored it word for word in
each task you own. Do "Hand-off", tell the user to continue in a new session.

## Finish session
Do nothing else the message asks. 1. "Hand-off". 2. Run the `report` command from the instruction; read the
weekly and 5-hour quota with the usage tool if there is one; add one row to "Session measurements" in
docs/STATUS_ARCHIVE.md ("n/a" for a value you cannot read; never invent one). 3. Tell the user: session closed.
