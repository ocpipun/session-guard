<!-- SESSION-GUARD: 2.0 | component: doc-readme | version: 2.0.0 -->
# Session Guard

**Version 2.0** · Open source, **MIT license** · [Türkçe](README.tr.md) · Design: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)

Session Guard is a Claude Code plugin. It measures what Claude cannot see (context size, breaks, quota), tells
Claude when to hand off and stop, and locks finished sessions. Version 2.0 also gives every project, session and
task an id, guards who owns which task, and makes every hand-off keep the unfinished work, so parallel or later
sessions never overwrite or lose each other's work.

## 1. Context thresholds
Session Guard reads the context size from the session's transcript after every message and every tool call and
gives Claude an instruction only when a threshold is passed. Claude never acts on a threshold early.

| Context | Normal mode | Long mode |
|---|---|---|
| 250k | notice | notice |
| 300k | hand-off of every task the session owns; then no new tasks, only questions | notice |
| 350k | target stop: open a new session | — |
| 400k | hard stop, session locked | — |
| 700k | — | notice; task notes kept current, no hand-off yet |
| 800k | — | hand-off of every task the session owns; then no work, only simple questions |
| 900k | — | hard stop, session locked |

- **Long mode:** type `start long mode`. It holds for this session only and cannot be switched off.
- **No second hand-off:** after the 300k hand-off (normal) or the 800k hand-off (long), `start long mode` and
  `finish session` are refused, so one session never leaves two hand-offs that contradict each other. The session
  measurement is taken with that hand-off.
- Every notice reaches the user as one line ("…threshold passed… *This is a Session Guard notice.") in the
  user's language.

## 2. Breaks
Normal mode, more than 1 hour since Claude's last reply:
- context over 175k: the message is **not** run. Session Guard stores it word for word in each task's hand-off,
  Claude hands off and the session is locked;
- 175k or less: a one-line reminder, then the message runs.

Waiting for Claude's own background job is not a break. Long mode has no break rule; "start long mode" written
right after such a break does not skip it (the session hands off first).

**Break measurement (optional, off by default).** To see what a resumed break costs you, ask Claude to run
`sg breaks on`. It applies to every session and project on this computer until you run `sg breaks off`. After each
gap over 1 hour Claude reads your quota (desktop app only) at the start and at the end of that turn; with the last
reading before the break it is written as one row to `~/.claude/session_guard_v2/break_measurements.jsonl`.
Nothing leaves your computer. `sg breaks show` lists the rows. Each reading costs a few tokens.

## 3. Finish session
`finish session` (at the start of a message followed by a comma or period, or at its end): Claude does nothing
else, makes every task hand-off ready, writes the hand-offs, takes the session measurement (Session Guard's
`report` line plus the 5-hour and weekly quota when the app can read them) and the session is locked. Until the
hand-offs are written and published, Claude is kept working (twice at most).

## 4. Quota (computer)
5-hour quota at 90% or weekly at 98%, with the reset 45 minutes or more away: Claude starts nothing new, records
where each task stopped, and after 15 minutes the session is locked until the reset. Desktop app: read from the
app's usage tool; terminal: from the status line, at no token cost. In the cloud the quota cannot be read (measured
2026-11-03: the status line does not run there and there is no usage tool); the app itself reports a used-up quota.

## 5. Session locks
A locked session gets no more prompts, and the user is told why. Kinds:
- context limit, finish session, break hand-off;
- quota (until the reset);
- handed over (every task was claimed by a newer session);
- started before the project moved to 2.0;
- wrong place (a computer project opened in the cloud, or the other way round);
- restart (a session that started before Session Guard was installed or updated: quit Claude fully and reopen it).

A 24-hour watch tells the user when a turn or a background job has been running for a day.

## 6. Projects, sessions and tasks
- **Ids:** `PRJ-XXXXXXXX` per project, `S-YYYYMMDD-xxxxxx` per session (also in `$SG_SESSION_ID`), tasks
  `TASK-001`, subtasks `TASK-001-1`, `TASK-001-2-1`. Ids are never reused.
- **Ownership:** only a task's owner changes it. A free task (no owner, owner ended or locked, or a hand-off
  waiting) can be claimed; a task of an active session only with the user's approval. Owner history is kept.
- **Limits:** at most 10 normal and 3 urgent main tasks (urgent: with a reason and the user's approval) for each
  agent working in the project at the same time (two agents: 20 + 6), 50 queued tasks, 50 ideas (temporary ids
  IDEA-xxx, replaced by the task id when an idea becomes a task). A multi-step project or a bulk prompt is split
  by Claude into this form. Subtasks, setup, migration and rule-script work are not counted. Over a limit Claude
  tells the user to finish or drop waiting work first.
- **Urgent first:** an urgent task goes to the top of the list and belongs to its agent: while it is active,
  that agent takes no new task (new requests are recorded as ideas); other agents go on.
- **Subagents get only subtasks:** a subagent (Agent tool) is launched only with a subtask of a task its session
  owns, named in its prompt; Session Guard refuses other launches, and a subagent cannot open a main task.
  Subagents and subtasks are not counted; a subagent may hand off and close its own subtask.
- **Child session agents:** a session started from another one is a separate agent under the same rules. Its
  kind is fixed at its start by the brief line `SG-PARENT: <id> bound` or `SG-PARENT: <id> independent`:
  - *Bound* (`<parent>-B<n>`): helps with the parent's work (a test, a part of it) and works only on subtasks of
    the parent's tasks. Before the parent hands off, `sg handoff children` lists its bound children and the message
    to send them; the parent writes its hand-off only after theirs, and they are locked with it.
  - *Independent* (`<parent>-I<n>`): its own main tasks, never the parent's work. When the parent stops, Session
    Guard tells it to make its work hand-off-ready (branch and PR in its hand-off) and it goes on.
  - The kinds never take each other's work. A session that takes over a hand-off is a continuation, not a child.
- **Compaction and forks:** after Claude Code compacts a conversation, Session Guard gives the rules and STATUS
  again, measures the smaller context and lets the one-time notices fire again. A fork (/fork, /branch) becomes a
  new session that keeps the mode (long mode too) and the context.
- **No duplicate work:** when the user gives new work (a bulk prompt at project start, or new tasks later),
  `sg task check` lists every running, urgent and queued task and idea, finished work sharing words with the new
  items (pre-2.0 items found by keywords) and near copies. Claude compares them and explains any overlap,
  conflict, contradiction or near copy to the user in their language; a near copy is refused.
- **Waiting work outside STATUS:** queued tasks are in `docs/TASK_BACKLOG.md`, ideas in `docs/IDEA_TASKS.md`; both
  are generated, one section per id, so entries change by id, never by line number. STATUS only shows their
  counts, so it stays short. A bullet written under "## Add an idea" in IDEA_TASKS.md (or under a "## Notes"
  heading of STATUS) becomes an idea with an id and is checked against the existing work.
- **What is read when:** session start CLAUDE.md and STATUS.md; new work only the check above; the archive only
  when needed, by search (2.0 entries by id, pre-2.0 entries by keywords).
- **New id when needed:** continuing a task keeps its id; redoing a wrongly finished task or extending a finished
  one is a new task linked to the old one. A finished subtask never finishes its parent.
- **Safe parallel writes:** one lock per project store, a fresh read inside the lock, revision checks; direct edits
  of the store are refused, and a hand-edited task block in STATUS.md is rebuilt from the store. A session acts
  only as itself: an `sg` command with another session's id is refused.
- **Same conversation, new id:** Claude Code's session id can change (the desktop app was seen copying a
  conversation to a new id; a cloud VM can be rebuilt). Session Guard finds the conversation again from its message
  ids, on this machine and in the project's own records, and keeps its SESSION-ID, tasks, long mode and locks.
- **Session start:** only STATUS.md is read. First Claude proposes finishing work that was handed off and not
  taken over yet (several: it recommends one, you choose). Only when none waits and you want new work: a queued
  task (TASK_BACKLOG.md); queue empty: an idea (IDEA_TASKS.md), designed with you by questions, put in the queue,
  then started. The backlog and ideas are read only then; the archive only when needed, by search.
- **Who worked on a task:** under each task in STATUS an "Agents" line lists every session that owned it, in order,
  with dates and "handed off" where it was handed over.
- **Hand-off notes:** every session that hands off work writes one short general note (`sg handoff note`): its
  signature, the date, the quota readings and its overall assessment. They stand at the bottom of STATUS, one per
  session (several agents: several notes). When all the work a note hands over is taken and a newer note is written,
  the old note goes to the archive as one line, so STATUS stays short in large projects.
- **Finished tasks:** each closes with a one-sentence result (`sg task done <id> --summary "..."`); COMPLETED_TASKS.md
  gets one line: date, id, title, result, agents. An agent whose tasks are all finished writes a "project state"
  note from that file instead of a hand-off note.

## 7. Hand-offs that keep the work
Each task gets its own hand-off: project, task and session ids, the session's parent and children, last
developer, status, done, pending, owner, branch, pull request, technical notes, the user's command word for word
(break), and **where the unfinished work is**. Before writing it Claude finishes the small step in progress, so
nothing is left half-written.

| | Computer | Cloud |
|---|---|---|
| Where the work goes | stays in the project folder; the hand-off lists the uncommitted files | committed and pushed to the task's own side branch `sg/<task-id>`, push verified |
| Side branch | only when the user asks or approves Claude's proposal | always (a cloud checkout is gone with its session) |
| Unsaved files at the end | — | refused: the hand-off is not done until every changed file belongs to a task |
| Next session | opens the same folder and claims the task | is told which side branches hold hand-offs, merges one (`sg handoff adopt`), then claims the task |

When a side branch is merged, conflicts in Session Guard's own files are resolved automatically (tasks are merged;
two different tasks that got the same id on two branches are renumbered); a conflict in the project's own files
stops the merge and changes nothing. Side branches `sg/<task-id>` hold the project's own unfinished work (its code), not Session Guard's files. Git never deletes a branch when it is merged (GitHub can, for a merged pull request, when the repository setting "Automatically delete head branches" is on), and a cloud session cannot delete branches at all (its GitHub proxy refuses deletions). Delete merged side branches by hand on GitHub. Tip: GitHub repository Settings > General > Pull Requests > "Automatically delete head branches" deletes a side branch after its pull request is merged (GitHub docs: a deleted head branch can be restored from the closed pull request with "Restore branch"). Session Guard tells the user at every session start which side branches are merged and can be deleted. Resetting or reverting the default branch does not change any other branch: each branch is its own pointer in git. When a newer session claims a task, the old session is locked once it owns no
open task. Claimed hand-offs go to `STATUS_ARCHIVE.md`.

## 8. Project files
| File | Role |
|---|---|
| `CLAUDE.md` | permanent rules and decisions |
| `docs/STATUS.md` | current state; task block generated by Session Guard; no line limit (the limits are task counts) |
| `docs/STATUS_ARCHIVE.md` | history, append only; never read at session start, only searched |
| `docs/TASK_BACKLOG.md` | queued tasks (generated, one section per TASK-ID) |
| `docs/IDEA_TASKS.md` | ideas (generated, one section per IDEA-ID) |
| `docs/COMPLETED_TASKS.md` | one line per finished task: result in one sentence, its agents |
| `.claude/session-guard2/store.json` | the task store, committed with the project |

## 9. Computer and cloud
A project lives either on the computer or in the cloud, never both. A computer project that uses git carries a
small hook that blocks cloud sessions of it. A cloud project carries a copy of Session Guard in
`.claude/session-guard2/bin/` ([CLOUD.md](CLOUD.md)); on a computer the plugin locks its sessions.

| Place | Works | Note |
|---|---|---|
| Desktop app, terminal (computer) | yes | everything; tested on Windows |
| Remote Control (phone or browser driving a computer session) | yes | the session runs on the computer (documented) |
| Cloud sessions (claude.ai/code, from browser, phone or desktop app) | per repository | repository copy; no quota protection; side branches for hand-offs |
| macOS / Linux | expected | written for them, not tested |

## 10. Install, update, migration
- Install or update only from a Claude project folder named `session-guard-executive` ([INSTALL.md](INSTALL.md)).
- 2.0 keeps the 1.x plugin id, so the update replaces 1.x; while 1.x is installed 2.0 stays passive (never two
  guards). 1.x locks are honoured; its global rules block is replaced by the 2.0 block.
- Before installing, the integrity list (`manifest.json`: id, version, path, type, SHA-256, dependencies and the
  `SESSION-GUARD: 2.0` signature of every file) is checked, and a table shows what belongs to 1.x and what 2.0
  replaces. One approval.
- **Migration**, one project at a time, every step approved: discover the projects (also ones found later),
  survey each (git state, instruction and status files, old sessions), a reading by a subagent, a plan (at most 10
  active tasks), a rollback point (git tag; file copies for every project), apply, verify, and a verified push of
  Session Guard's own files only. Conversations that began before a project's migration are locked.
- **Budget:** an installation session may use about 800k tokens; at 90% it stops at a safe point and the user
  continues in a new session with `continue installation`.
- Notices can be shown in the user's language (`sg install language`).
- Old task lists are found by content (checkboxes, "To do / Pending / Done" headings), whatever the file is
  called. Their open items become tasks, queued tasks or ideas, finished items lines of `COMPLETED_TASKS.md`; ids
  show they come from before 2.0 (`PRE20-DONE-xxx`, `PRE20-IDEA-xxx`, tasks marked "pre-2.0"). With the user's yes
  a file that only listed tasks is deleted whole (no copy), and task text inside other files is cut out, so no
  agent follows an old list after the migration. CLAUDE.md lines that repeat a status or task line are listed;
  state leaves CLAUDE.md, so a rule and a task are never copies of each other.

## 11. Uninstall
Every step with the user's yes ([setup skill](plugins/session-guard/skills/setup/SKILL.md), "Uninstall").
- **Computer** (from `session-guard-executive`): open tasks and hand-offs of every project are shown first. Per
  project: `sg install remove-project` keeps STATUS.md's last task list as plain text plus the archive, backlog
  and completed list, and removes Session Guard's hooks, copy, skills copies and setup marker (`--remove-store`
  also the task store). Then `sg install uninstall` (rules block, status line; `--purge-state` also the machine
  state) and `claude plugin uninstall session-guard@session-guard`, then a full restart.
- **Cloud repository:** `sg install remove-project` in a session of it, commit, push or pull request; old cloud
  sessions are archived at claude.ai/code.
- **Warning:** Side branches `sg/<task-id>` hold the project's own unfinished work (its code), not Session Guard's files. Git never deletes a branch when it is merged (GitHub can, for a merged pull request, when the repository setting "Automatically delete head branches" is on), and a cloud session cannot delete branches at all (its GitHub proxy refuses deletions). Delete merged side branches by hand on GitHub.

## 12. Rules become scripts
Every rule line of CLAUDE.md gets an id (`<!-- R-001 -->`). Whenever CLAUDE.md gets new or changed rules (also
when it is first created), Session Guard asks Claude for a rule review: conflicts with the other rules (update or
merge, decided with the user) and, for every rule a script or hook could check, a script proposal as an idea (not
counted in the idea limit). The user decides; a script task is done only when the script is verified to work, and
then its rule leaves CLAUDE.md. So checks move from Claude (tokens every session) to scripts (no tokens).
STATUS.md and CLAUDE.md are edited by section and id, never by line number; empty lines and empty bullets left by
an edit are removed.

**Session Guard's rules come first.** Claude Code loads every instruction file side by side and does not rank
them, so two contradicting rules would leave Claude to choose. Session Guard therefore never leaves a contradiction
in place: a rule in any instruction file (project or global) that contradicts or overlaps a Session Guard rule is
removed (at migration, at install for the global file, and at every rule review), and a new rule that would
contradict Session Guard is not added. Each removal is shown to the user and recorded in the archive. What hooks
enforce (thresholds, locks, limits, ownership) holds whatever any file says.

## 13. Commands
User: `start long mode`, `finish session`, `continue installation` (at the start followed by a comma or period, or
at the end of a message). Claude uses `sg task …`, `sg idea …`, `sg rules check|reviewed`,
`sg handoff children|write|note|publish|adopt`, `sg breaks`, `sg migrate …`, `sg install …`, `sg manifest verify` and
`sg report` itself. The full list is at the end of this file (§16).

## 14. Limits
- The transcript format Session Guard reads is not documented by Anthropic and may change.
- Old cloud sessions cannot be locked from the computer; archive them at claude.ai/code.
- Projects without git have no git rollback; Session Guard keeps file copies only.
- Locks work through hooks: a session without them is not locked.
- A side branch reaches the default branch only when the user merges it (or a pull request); until then a session
  started from the default branch sees that hand-off after `sg handoff adopt`.
- What could not be verified: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) §8.

## 15. Tests
```bash
python -m unittest discover -s tests
```

## License
MIT ([LICENSE](LICENSE)). Provided as is, without warranty.

## 16. All commands
User commands, typed in a message (at its start followed by a comma or period, or at its end):
- `start long mode` — long mode for this session (cannot be switched off)
- `finish session` — hand-offs, session measurement, then the session is locked
- `continue installation` — continues a paused installation (only in `session-guard-executive`)

`sg` commands. `sg` stands for `python "<plugin>/scripts/session_guard.py"`; Claude gets the exact command at
session start and runs these itself. You can ask Claude to run any of them.

Tasks and ideas
- `sg task list [--all]` — the project's tasks
- `sg task show <TASK-ID>` — one task with its hand-off
- `sg task check --title "..." [--title "..."]` — new work compared with all existing work
- `sg task new --title "..." --approval "..." [--priority P1|P2|P3] [--kind urgent --reason "..."] [--queue]` — a main task
- `sg task new --title "..." --parent <TASK-ID>` — a subtask
- `sg task start <TASK-ID>` — a queued task becomes active
- `sg task claim <TASK-ID> [--takeover "<the user's words>"]` — take over a task
- `sg task note <TASK-ID> [--done ...] [--pending ...] [--resolved ...] [--status ...] [--text ...]` — update a task
- `sg task done <TASK-ID> --summary "<result, one sentence>" [--verified ...] [--force]` / `sg task cancel <TASK-ID> --summary "<why>"` — close a task
- `sg task reopen <TASK-ID> --title "..." [--relation reopens|extends]` — redo or extend as a new linked task
- `sg task relate <TASK-ID> --other <TASK-ID> [--relation ...]` — link two tasks
- `sg idea add "<text>" [--source ...] [--rule R-xxx] [--force ...]` — record an idea
- `sg idea promote <IDEA-ID> --approval "..." [--queue]` — an idea becomes a task

Hand-offs
- `sg handoff children` — before a hand-off: the child session agents and the message for the bound ones
- `sg handoff write <TASK-ID> --technical "..." [--paths ...] [--no-files] [--side-branch] [--pr <link>] [--children-gone "..."]` — write a task's hand-off
- `sg handoff note --text "..." [--project-state]` — the session's general note (signature, date, quota added); project state when all its tasks are finished
- `sg handoff publish` — publish the hand-offs (side branches pushed and verified; refused without the note)
- `sg handoff adopt sg/<task-id>` — merge a side branch with its work and hand-off

Rules
- `sg rules check` — CLAUDE.md rule lines not reviewed yet
- `sg rules reviewed` — record the review (gives rule ids)
- `sg rules remove <R-ID> --approval "..."` — remove a rule line (with the user's yes)
- `sg rules remove <R-ID> --conflict "<Session Guard rule>"` — remove a rule that contradicts Session Guard

Break measurement
- `sg breaks on` / `sg breaks off` / `sg breaks show`

Install, migration, uninstall (from `session-guard-executive`, each with the user's yes)
- `sg manifest verify` (`sg manifest build` only for developers)
- `sg install conflicts` · `sg install approve --executive "<folder>"` · `sg install language [<code>]` ·
  `sg install messages <file>`
- `sg install cloud "<repository>"` · `sg install home-guard "<folder>"`
- `sg install remove-project "<folder>" [--remove-store]` · `sg install uninstall [--purge-state]`
- `sg migrate discover [--add <folder>]` · `sg migrate status` · `sg migrate survey "<folder>"` ·
  `sg migrate plan-help` · `sg migrate apply "<folder>" --plan <file>` · `sg migrate verify "<folder>"` ·
  `sg migrate push "<folder>" --approval "..."` · `sg migrate pause --reason ...` ·
  `sg migrate skip "<folder>" --reason ...` · `sg migrate finish` · `sg migrate start --claude-session <id>`

Session measurement
- `sg report "<project folder>"` — one line for "finish session"

Common options: `--session <SESSION-ID>` (only the session's own id), `--root <project folder>`,
`--expect-rev <n>`.
