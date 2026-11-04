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
- context over 100k: the message is **not** run. Session Guard stores it word for word in each task's hand-off,
  Claude hands off and the session is locked;
- 100k or less: a one-line reminder, then the message runs.

Waiting for Claude's own background job is not a break. Long mode has no break rule.

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
- **Limits:** at most 3 normal main tasks, plus 1 urgent one (with a reason and the user's approval) when 3 are
  active; subtasks unlimited; setup and migration tasks exempt. A new main task needs the user's approval;
  unrelated work goes to the backlog (`docs/TASK_BACKLOG.md`).
- **New id when needed:** continuing a task keeps its id; redoing a wrongly finished task or extending a finished
  one is a new task linked to the old one. A finished subtask never finishes its parent.
- **Safe parallel writes:** one lock per project store, a fresh read inside the lock, revision checks; direct edits
  of the store are refused, and a hand-edited task block in STATUS.md is rebuilt from the store.
- **Session start:** Claude gets its ids, its tasks and the tasks it could take over; when several can be taken,
  Claude asks the user which one.

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
stops the merge and changes nothing. When a newer session claims a task, the old session is locked once it owns no
open task. Claimed hand-offs go to `STATUS_ARCHIVE.md`.

## 8. Project files
| File | Role |
|---|---|
| `CLAUDE.md` | permanent rules and decisions |
| `docs/STATUS.md` | current state; task block generated by Session Guard; at most 50 lines (no new task above it) |
| `docs/STATUS_ARCHIVE.md` | history, append only; never read at session start, only searched |
| `docs/TASK_BACKLOG.md` | waiting ideas |
| `docs/COMPLETED_TASKS.md` | one line per finished task |
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
  survey each (git state, instruction and status files, old sessions), a reading by a subagent, a plan (at most 3
  active tasks), a rollback point (git tag; file copies for every project), apply, verify, and a verified push of
  Session Guard's own files only. Conversations that began before a project's migration are locked.
- **Budget:** an installation session may use about 800k tokens; at 90% it stops at a safe point and the user
  continues in a new session with `continue installation`.
- Notices can be shown in the user's language (`sg install language`).
- Uninstall: `sg install uninstall`, then `claude plugin uninstall session-guard@session-guard`; project files stay.

## 11. Commands
User: `start long mode`, `finish session`, `continue installation` (at the start followed by a comma or period, or
at the end of a message). Claude uses `sg task …`, `sg idea …`, `sg handoff write|publish|adopt`, `sg migrate …`,
`sg install …`, `sg manifest verify` and `sg report` itself.

## 12. Limits
- The transcript format Session Guard reads is not documented by Anthropic and may change.
- Old cloud sessions cannot be locked from the computer; archive them at claude.ai/code.
- Projects without git have no git rollback; Session Guard keeps file copies only.
- Locks work through hooks: a session without them is not locked.
- A side branch reaches the default branch only when the user merges it (or a pull request); until then a session
  started from the default branch sees that hand-off after `sg handoff adopt`.
- What could not be verified: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) §8.

## 13. Tests
```bash
python -m unittest discover -s tests
```

## License
MIT ([LICENSE](LICENSE)). Provided as is, without warranty.
