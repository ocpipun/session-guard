<!-- SESSION-GUARD: 2.0 | component: doc-readme | version: 2.0.0 -->
# Session Guard

**Version 2.0** · Open source, **MIT license** · [Türkçe](README.tr.md) · Design: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)

Session Guard is a Claude Code plugin. It measures what Claude cannot see (context size, breaks, quota), tells
Claude when to hand off and stop, locks finished sessions, and, new in 2.0, gives every project, session and task
an id and guards who owns which task, so parallel or later sessions never overwrite each other's work.

## What it does

### Context thresholds (unchanged from 1.4)
| Context | Normal mode | Long mode |
|---|---|---|
| 250k | notice | notice |
| 300k | hand-off of every task the session owns; no new tasks | notice |
| 350k | target stop | — |
| 400k | hard stop, session locked | — |
| 700k | — | notice; work made hand-off ready |
| 800k | — | no work, simple questions, final hand-offs |
| 900k | — | hard stop, session locked |

### Breaks (unchanged from 1.4)
Normal mode, gap over 1 hour: context over 100k → the message is not run; Session Guard itself stores it word for
word in each task's hand-off and locks the session. 100k or less → one-line reminder, then the message runs.
Waiting for Claude's own background job is not a break. Long mode: no break rule.

### Quota (computer, unchanged from 1.4)
5-hour 90% / weekly 98% with the reset 45+ minutes away: the current step is brought to a safe point, then the
session is locked until the reset. Desktop app: read from its usage tool; terminal: from the status line.

### Projects, sessions, tasks (new)
- **Ids:** `PRJ-XXXXXXXX` per project, `S-YYYYMMDD-xxxxxx` per session, `TASK-001`, `TASK-001-1`, `TASK-001-2-1`.
- **Ownership:** only a task's owner changes it. A free task (no owner, owner ended, or a hand-off waiting) can
  be claimed; one owned by an active session only with the user's approval. History is kept.
- **Limits:** at most 3 normal main tasks, plus 1 urgent one (reason and approval) when 3 are active; subtasks
  unlimited; setup and migration tasks exempt. Unrelated work goes to the backlog.
- **New id when needed:** continuing a task keeps its id; redoing a wrongly finished task or extending a
  finished one is a new task linked to the old one. A finished subtask never finishes its parent.
- **Hand-offs per task**, published with a verified git push; the next session asks the user when several can be
  taken over. When a task's hand-off is claimed, the old session is locked.
- **Safe parallel writes:** one lock per project store, revision checks, generated STATUS; direct edits of the
  store are refused, a hand-edited task block is rebuilt.

### Project files
| File | Role |
|---|---|
| `CLAUDE.md` | permanent rules and decisions |
| `docs/STATUS.md` | current state; task block generated, at most 50 lines |
| `docs/STATUS_ARCHIVE.md` | history, append only, searched, never read whole |
| `docs/TASK_BACKLOG.md` | waiting ideas |
| `docs/COMPLETED_TASKS.md` | one line per finished task |
| `.claude/session-guard2/store.json` | the task store (committed with the project) |

## Where it works
| Place | Works | Note |
|---|---|---|
| Desktop app, terminal (computer) | yes | all features; tested on Windows |
| Remote Control | yes | the session runs on the computer (documented) |
| Cloud sessions (claude.ai/code) | per repository | repository copy; no quota protection |
| macOS / Linux | expected | written for them, not tested |

A project lives either on the computer or in the cloud, never both (1.4 rule, kept).

## Install and migration
Only in a Claude project folder named `session-guard-executive`. Session Guard 2.0 is installed with the same
plugin id as 1.x, so the update replaces 1.x; while 1.x is still installed 2.0 stays passive. Every project then
goes through a migration (survey → plan → rollback point → apply → verify → push), each step with the user's
approval: [INSTALL.md](INSTALL.md). Cloud: [CLOUD.md](CLOUD.md).

## Commands
Type at the start (then a comma or period) or at the end of a message: `start long mode`, `finish session`,
`continue installation`. Claude uses `sg task ...`, `sg handoff ...`, `sg migrate ...` itself.

## Limits (see docs/ARCHITECTURE.md §7–8)
- The transcript format Session Guard reads is not documented by Anthropic and may change.
- Old cloud sessions cannot be locked from the computer; archive them at claude.ai/code.
- Projects without git have no git rollback; Session Guard keeps file copies only.
- Locks work through hooks: a session without them is not locked.

## Tests
```bash
python -m unittest discover -s tests
```

## License
MIT ([LICENSE](LICENSE)). Provided as is, without warranty.
