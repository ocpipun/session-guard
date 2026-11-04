<!-- SESSION-GUARD: 2.0 | component: doc-architecture | version: 2.0.0 -->
# Session Guard 2.0 — architecture, migration plan, risks

Status: 2.0.0 on `main` (2026-11-04). Real use starts with the user's own install; no real project migrated yet.

## 1. Layers

| Layer | Where | What |
|---|---|---|
| Measurement (from v1.4) | `sg2/measure.py` | context from the transcript, break gap, background jobs, quota readings, thresholds |
| Session records | `~/.claude/session_guard_v2/sessions/` (`sg2/sessions.py`) | SESSION-ID per Claude session, long mode, locks |
| Project store | `<repo>/.claude/session-guard2/store.json` (`sg2/store.py`) | PROJECT-ID, sessions, TASK-IDs, owners, hand-offs, ideas, revision |
| Project files | `docs/*.md` (`sg2/render.py`) | generated from the store; archive and completed list append-only |
| Hooks | `sg2/hooks.py` | SessionStart, UserPromptSubmit (+24 h watch), PreToolUse, PostToolUse, Stop, SessionEnd |
| Commands | `sg2/cli.py` (`sg task / idea / handoff / migrate / install / manifest / report`) | every change of tasks |
| Migration | `sg2/migrate.py`, `~/.claude/session_guard_v2/migration.json` | discover, survey, apply, verify, push, checkpoint, budget |
| Integrity | `manifest.json`, `sg2/manifest.py` | id, version, path, type, SG version, sha256, dependencies; signature check |

Measurement works in every project from install on (no gap in protection). PROJECT-ID, tasks and hand-offs
start per project only after its migration is verified.

## 2. Ids
- PROJECT-ID `PRJ-XXXXXXXX` (random), in the store, committed with the repository, so every place sees one id.
- SESSION-ID `S-YYYYMMDD-xxxxxx` per Claude session (documented `session_id`); kept on resume; a reopened
  conversation under a new id is found through its message ids (v1.4). Parent/child: a session that claims a
  hand-off becomes the child of its source; other sessions stay independent (no forced chains).
- TASK-ID `TASK-001`, `TASK-001-1`, `TASK-001-2-1`. Counters only grow; ids are never reused. Old 1.x session
  ids are listed in `legacy_sessions` only.

## 3. Task rules (enforced in code, tested)
- Owner only changes a task; claiming is free when there is no owner, the owner ended or is locked, or a hand-off
  waits; an active owner keeps it unless the user approves a takeover. Owner history is never deleted.
- At most 3 normal + 1 urgent (reason + approval, only when 3 normal are active) main tasks; setup and migration
  tasks exempt; subtasks unlimited. A new main task needs the user's approval text; unrelated work → backlog.
- A subtask closing never closes its parent; a parent with open subtasks closes only with `--force`.
- Redo/extension of a finished task = new id with `reopens`/`extends` link both ways.
- Concurrency: one O_EXCL lock file per store, fresh read inside the lock, validation, revision + atomic replace;
  `--expect-rev` refuses a decision made on an older read. Direct edits of the store are denied (PreToolUse);
  a hand edit of the STATUS task block is rebuilt from the store (PostToolUse / next prompt).

## 4. Hand-off
Fields: PROJECT-ID, TASK-ID, source SESSION-ID, its parent/children, last developer, status, done, pending,
owner, branch, PR (gh when available), technical notes (required), the user's command word for word (break), and
the work record (where the unfinished work is).
Work first (user decision 2026-11-04): before the note, Claude brings the work to a hand-off-ready point; then
`sg handoff write` records the work:
- computer (default): mode "folder": path, branch, commit, the uncommitted files. No commit, no push: the next
  session opens the same folder (side branches would cost tokens for nothing there).
- cloud (always) or computer with `--side-branch` (only when the user asks or approves): mode "side-branch": the
  task's changed files are committed (`--paths` when the session owns several tasks) and `sg handoff publish`
  pushes HEAD to `sg/<task-id>` (verified with `git ls-remote`). In the cloud, publish refuses while any changed
  file is unsaved, and the Stop hook keeps Claude working (twice at most) after finish, a break or the 300k/800k
  hand-off while files are unsaved or hand-offs unpublished.
States: prepared → published (side branch, push verified) or local (kept in the folder) → claimed (archived in
STATUS_ARCHIVE.md, append only). Not repeated when unchanged or when the task is finished. The source session is
locked when its last open task is claimed.
Taking over in the cloud: a new session starts from the default branch, which has neither the work nor the
hand-off. Session start lists the remote `sg/*` branches the checkout does not contain ("HAND-OFF BRANCHES");
`sg handoff adopt <branch>` merges one. Conflicts in Session Guard's files are resolved there: store.json by
`store.merge` (tasks: the copy changed last; sessions: the strongest state, a lock is never undone; counters never
go back; two different tasks that got one id on two branches: the other branch's task and its subtasks get the
next free ids, references rewritten, a note added), archive and completed list by the union of lines, STATUS and
backlog rebuilt. A conflict in any other file aborts the merge and changes nothing. A renumbered task keeps its
side branch name.

## 5. v1.4 review

| Component | Verdict | Note |
|---|---|---|
| Context measurement, thresholds, long mode | REUSED, CHANGED | numbers kept; hand-off per task; long mode hands off at 800k (1.4: 700k while work went on); after the 300k/800k hand-off "start long mode" and "finish session" are refused (user decision 2026-11-03: no second, stale hand-off of one session) |
| Break rule | REUSED, CHANGED | the hook itself stores the user's command word for word in each owned task |
| Quota (Session Follow) | DIRECTLY REUSED | same thresholds; state moved to `session_guard_v2/follow` |
| Session lock types | REUSED, EXTENDED | + handed, legacy, home |
| Reopened conversation by message ids | DIRECTLY REUSED | |
| 24-hour watch (asyncRewake) | DIRECTLY REUSED | |
| Restart lock (session older than install) | DIRECTLY REUSED | |
| Rules text, reply style, archive rule | REUSED, CHANGED | ids, tasks, generated STATUS added |
| Global CLAUDE.md block | REUSED, CHANGED | new markers `session-guard2`; 1.x block removed only after 2.0 block exists |
| Notices / languages | DIRECTLY REUSED | new notices; tr.json extended |
| Project files creation at session start | REMOVED | 2.0 never writes into a project before its migration |
| One home per project (computer/cloud) | REUSED, CHANGED | `home` field in the store; cloud-blocking hook `session-guard2-home` (replaces the 1.x one in place) |
| Setup gate, conflict scan (`--scan`) | REWRITTEN | install + migration survey (content-based) |
| `--push` / `--claim` | REWRITTEN | `gitops.commit_paths` + `push_verified`; work kept per task (folder / side branch), `handoff adopt` |
| Hand-off note | REUSED, CHANGED | per task; work made hand-off ready first; cloud work on `sg/<task-id>` (user decision 2026-11-04) |
| `cloud_setup.py` (repository copy) | REWRITTEN | `sg install cloud` (`sg2/place.py`): copy in `.claude/session-guard2/bin/`, 1.x copy hooks replaced, quiet beside the plugin |
| Terminal status line | REUSED, CHANGED | mark `session-guard2-statusline`; a 1.x line is re-pointed, another tool's line untouched |
| v1.2 design doc, v1.4 tests | REMOVED on the branch | still on `main` |

## 6. Old/new conflicts (checked by `sg install conflicts`, tested)
- Plugin id stays `session-guard@session-guard`: `claude plugin update` replaces 1.4 with 2.0; while any 1.x entry
  is installed, 2.0 is passive (no double guard).
- State: 1.x `~/.claude/session_guard/` is read only (its finish/break/context locks are honoured); 2.0 writes only
  `~/.claude/session_guard_v2/`.
- Global block: different markers; removal of the 1.x block refused unless the 2.0 block is present.
- Repository copies: 1.x cloud copy (`.claude/session-guard/`) stays quiet on a computer because 2.0's entry is
  still named `session_guard.py` (v1.4 `plugin_installed()`); migration verify refuses while it is present.
- Hooks in user settings that run a `session_guard.py` (hand-made copies) are listed for approval.
- Status line: a 1.x line is re-pointed to 2.0 at install; another tool's line is left alone.
- Cloud: the repository copy guards; a plugin that reaches a cloud session stays quiet (no "setup pending" noise
  in other repositories). On a computer the copy stays quiet when the plugin is installed. A 1.x global block
  written back by a 1.x session after the update is removed at the next 2.0 session start.
- Read-only discovery on the development computer (2026-11-03): 12 projects (7 git with the 1.x computer guard,
  5 without git), 1.x plugin, state folder, global block and status line present; nothing was written.

## 7. Migration plan and risks
Order: install approve (control project) → discover → per project survey → subagent reading → plan → user yes →
apply (git tag + file copies) → verify → user yes → push (verified) → next project → finish. The checkpoint lives
in `migration.json`; at 90% of 800k tokens per installation session a safe stop, at 100% an automatic pause;
while paused every prompt is blocked except "continue installation" in `session-guard-executive`. Projects first
seen later (a session start in an unknown project) join the list as "found later".

Risks:
1. Old cloud sessions cannot be locked from the computer (no documented API). Only the user can archive them.
   A cloud session already open keeps its own checkout; it can still push to the repository.
2. Non-git projects: no rollback beyond the file copies in `session_guard_v2/backups/`.
3. The transcript schema is undocumented; a Claude Code change can break context/break measurement (as in 1.4).
4. Locking is enforced only by hooks: a session started with hooks disabled or a different plugin set is not
   locked.
5. Git merge conflicts in `store.json` between branches are not resolved automatically.
6. The plan is written by Claude from file contents; contradictions are listed, not resolved; the user approves.
7. Side branches are merged into the default branch by the user (or a pull request); they pile up until then. A
   closed task's branch stays listed at session start until it is merged.
8. Parallel cloud sessions keep separate copies of the store; they meet only when a branch is merged
   (`handoff adopt` or the user's merge). A git merge done by hand outside `handoff adopt` can still conflict in
   store.json.

## 8. Not verifiable / unverified
- `CLAUDE_CODE_ENTRYPOINT` (desktop detection) is not documented.
- Transcript entry format (context, message ids, background jobs) is not documented.
- Whether two installed plugins' hooks for one event both run is only implied by the docs.
- Locking or archiving a cloud session through an API: not documented (none found).
- `UserPromptSubmit` inside subagents: not documented. Hooks inside subagents carry `agent_id` (documented).
- A blocked prompt is still written to the transcript ("Original prompt:"), it is not erased (documented).
- SessionEnd runs with a short budget and cannot block; a crash may skip it (then tasks are not marked orphaned).
- The desktop app's own sessions sidebar archive is not a lock (user requirement; nothing can be checked here).
- Remote Control: hooks run on the computer (documented); `CLAUDE_CODE_BRIDGE_SESSION_ID` only during an active
  connection.
- Cloud pushes: the GitHub proxy "doesn't limit which branches a push can update" (documented); a protected branch
  can still refuse. Pushing `sg/*` from a real cloud session: not tested yet.
- Whether a cloud session receives the user's plugins (a variable `CLAUDE_CODE_SYNC_PLUGINS` exists there, seen by
  name only): not documented; 2.0 stays quiet in that case either way.
- Quota in the cloud: measured 2026-11-03, not readable (no status line run, no usage tool).

## 9. Install / uninstall (to be done only with the user's approval)
Install: in `session-guard-executive`, `sg manifest verify`, `sg install conflicts`, one approval, `sg install
approve --executive <folder>`, notices, migration, then `claude plugin marketplace update session-guard` +
`claude plugin update session-guard@session-guard`, full restart.
Uninstall (user decision 2026-11-04, skill setup "Uninstall"): first the open tasks and hand-offs of every
project are shown; per project `sg install remove-project` (STATUS block kept as plain text, archive / backlog /
completed kept; computer guard and copy hooks, the copy, skills copies, setup marker removed; `--remove-store` also
the task store); then `sg install uninstall` (2.0 and 1.x global blocks, status line; `--purge-state` the machine
state) and `claude plugin uninstall session-guard@session-guard`. Cloud repositories: remove-project, commit,
push or pull request; `sg/*` branches are deleted by the user on GitHub (the cloud proxy refuses deletions).

## 10. Migration of old task files, rules and STATUS hygiene (user decision 2026-11-04)
- The survey finds task lists by content (checkboxes, task headings in any file name, depth 3) and CLAUDE.md lines
  that repeat a status or task line (`duplicates`, similarity 0.85). The plan moves every item (tasks, ideas,
  completed lines), names files to retire (removed after apply, copy in the backup folder, the deletion committed
  with the migration push) and CLAUDE.md lines that are state (removed, copy kept). A rule lives only in CLAUDE.md,
  a task or state only in Session Guard's files.
- Rule review: the store keeps the reviewed CLAUDE.md rule lines (`rule_review`, set at migration from the plan's
  `rule_scripts`). New or changed lines trigger one RULE REVIEW request (session start, or right after an edit of
  CLAUDE.md): Claude proposes a script or hook per enforceable rule as a backlog idea, then `sg rules reviewed`.
- STATUS hygiene: text outside the generated block is tidied on every render and after every edit of STATUS.md
  (empty bullets, runs of empty lines and gaps inside a list removed, trailing spaces cut): an agent that deletes
  lines by number to keep the other numbers leaves exactly these. STATUS is edited by section.
Rollback of a migrated git project: `git reset --hard sg2-pre-migration-<date>` after saving later work.
