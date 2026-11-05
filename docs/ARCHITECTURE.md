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
  conversation under a new id is found through its message ids (v1.4). Identity from messages (user decision
  2026-11-05; seen twice: the desktop app copied a conversation to a new id and the first hook ran on the unfinished
  copy, so v1.4 made a fresh normal-mode record and stopped a long-mode session at 400k): the machine record keeps
  the last and the conversation's first assistant message ids; a record made without a match stays "unlinked" for
  30 minutes and looks again on every prompt and tool call once the transcript shows older history, matching only a
  session born earlier (a later one is a copy, handled as a fork). The project store keeps each session's first
  message ids and long mode, so a session is found again on another machine (cloud VM rebuilt, ~/.claude lost);
  the id made meanwhile becomes an alias (`renamed_to`). Cloud (user decision 2026-11-05): the machine records also
  go to the remote branch `sg-state` (`sessions/<claude id>.json`) when the SESSION-ID, mode, locks, notices or
  first message ids change: `sg state-push` in the background, plumbing commit into a temporary index (work tree,
  index and current branch untouched), push without force, retried after a race. A cloud session start without a
  local record fetches it and writes back the records this machine lacks. Parent/child: a session that claims a
  hand-off becomes the child of its source; other sessions stay independent (no forced chains).
- TASK-ID `TASK-001`, `TASK-001-1`, `TASK-001-2-1`. Counters only grow; ids are never reused. Old 1.x session
  ids are listed in `legacy_sessions` only.

## 3. Task rules (enforced in code, tested)
- Owner only changes a task; claiming is free only when a hand-off waits or there is no owner (user decision
  2026-11-05: no agent takes work its owner has not handed off); an owner that is active, ended or locked without
  a hand-off keeps it unless the user approves a takeover. A session that hands off (300k / 800k, break, break
  watch, finish, hard stop; store `stopped`) or is locked gets no new task, start, claim or promotion. The break
  watch locks the session after its hand-off (Stop hook), not before. Owner history is never deleted.
- At most 10 normal and 3 urgent main tasks per working agent (active sessions owning an active main task, plus
  the asking one; subagents are not agents) (urgent: reason + approval, listed first; while one is active no new, queued, started or promoted
  task); setup and migration tasks exempt; subtasks unlimited. A new main task needs the user's approval text;
  unrelated work → ideas.
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
Hand-off before any lock (user decision 2026-11-05): break rule, hard limits (400k / 900k) and the break watch
mark the session "handing" (store `stopped`; PreToolUse, matcher "*", lets only `sg handoff` / `task show|note|list`
run); the Stop hook locks it once every hand-off is written and published. Without open work the hard limit stops at
once as before. Closed without a hand-off: at a computer session start, other machine records with work not handed
off, not running (idle over 10 minutes), and the break rule (normal, over 1 hour, over 175k) or a context hand-off
threshold (300k / 800k long) applying, are listed "AUTOMATIC HAND-OFF NEEDED" (once per 30 minutes); Claude sends each
`SG-AUTO-HANDOFF <SESSION-ID>` through the app's session messaging tool; that prompt makes it hand off only, then it
is locked. A headless `claude -p --resume` was tried and refused: the app's bundled CLI is not logged in (measured
2026-11-05). In the cloud a language chosen there and Claude's translations are kept in the project and on sg-state.
Language (user decision 2026-11-05): every notice to the user comes in their language (no English-only notice); a cloud VM has no config of its own, so `sg install cloud` writes the computer's language and Claude's own translations into `.claude/session-guard2/` (locale.json, messages/), read there in the cloud.
Break watch (user decision 2026-11-05): a Stop hook (async, asyncRewake, timeout 3900 s) waits 55 minutes after the
last reply; a newer turn end or a prompt replaces it (token file). Normal mode, over 175k, no own background job,
the turn closed: exit 2 wakes Claude ("BREAK HAND-OFF NOW"); the break lock is set at its first PostToolUse, so a wake
that never comes leaves the session as it was (the break rule acts at the next prompt). Not in the cloud (the VM
pauses after a few minutes). Idle line (2a): session start lists the project's other active sessions owning open
tasks without a hand-off, idle over one hour (machine record: last reply, context, mode, locks).
Session notes (user decisions 2026-11-05): after its task hand-offs a session writes ONE general note (`sg handoff
note --text`; signature, date, quota snapshot added; at most 600 characters); `handoff publish` refuses without one
listing every prepared hand-off. Several stand at once (one per session) under "Hand-off notes" in STATUS. A new
note retires (archive, one line) every older note none of whose tasks still waits with that session's hand-off, and
every older project-state note. An agent whose last task is closed writes `--project-state` (from
COMPLETED_TASKS.md). Each task shows an "Agents" line from its owner history. `task done|cancel` needs `--summary`
(one sentence, at most 200 characters): COMPLETED_TASKS.md gets the result and the agents in order. Session start:
claimable work is proposed first (urgent, then priority); with none, the backlog, then the ideas.
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
| Break rule | REUSED, CHANGED | the hook itself stores the user's command word for word in each owned task; lock over 175k (was 100k, user decision 2026-11-05: a fresh session starts near 78k); decided by the mode before the message, so "start long mode" cannot skip it (v1.4 bug) |
| Break measurement | NEW | off by default, for the whole computer (every session and project); `sg breaks on` / `sg breaks off` write ~/.claude/session_guard_v2/config.json; after a gap over 1 hour Claude reads the quota twice in that turn; with the last reading before the break one row goes to break_measurements.jsonl on that computer (nothing is sent); `sg breaks show` shows them; in README. UNVERIFIED: whether the first reading already includes the turn's first request |
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
- Limits (user decisions 2026-11-04): 10 normal + 3 urgent active per working agent, 50
  queued (status "queued", `sg task start`), 50 waiting ideas (temporary IDEA ids, the task id replaces them on
  promotion); rule-script tasks and rule-review ideas, subtasks, setup and migration not counted. The per-agent
  limit is checked when a task is created or started, not in validate(), so a session ending never blocks the
  store. Over a limit `sg` refuses and tells Claude to send the user to the waiting work. New tasks and ideas
  are compared (difflib) with all tasks and ideas: 0.85+ refused as a copy, 0.45+ listed for a conflict check.
- Rules by id: `rules.mark_reviewed` writes "<!-- R-xxx -->" at the end of each new rule line; the store keeps
  each id's reviewed text and state; a changed text is reviewed again. A rule-script task closes only with
  `--verified`, which removes its rule line (event "rule_replaced").
- Waiting work (user decisions 2026-11-04): queued tasks are rendered into TASK_BACKLOG.md, ideas into
  IDEA_TASKS.md, one heading per id; the STATUS block shows their counts. Bullets under "## Add an idea"
  (IDEA_TASKS.md) or "## Notes" (STATUS) become ideas after an edit; refused ones stay with the reason; any
  other hand edit of the two generated files is undone.
- Subagents (user decision 2026-11-04): PreToolUse on Agent/Task denies a launch whose prompt names no active
  subtask (TASK-xxx-n) owned by the session (web-fetch exempt); PreToolUse on Bash denies `task new` without
  `--parent`, `task start|claim|reopen` and `idea promote` when the input carries `agent_id` (documented: hooks
  run inside subagents with agent_id / agent_type). Sessions started from a session register on their own and are
  not locked with it: each session has its own record and lock (the store's parent/children fields only link).
- A session acts only as itself (review 2026-11-05): PreToolUse refuses an `sg` Bash command whose --session is
  another session's id (its own first id, the alias of a child id, counts as its own). Hook-time git queries
  (ls-remote at session start) run with GIT_TERMINAL_PROMPT=0 and GCM_INTERACTIVE=never: no credential prompt.
  CLAUDE.md rule ids are never written into table rows, comments of several lines or indented code.
- Urgent belongs to its agent (user decision 2026-11-04): only the owner of an active urgent task is refused new,
  queued, started or promoted tasks; other agents go on.
- Child session agents (user decisions 2026-11-04): a brief line "SG-PARENT: <id> bound|independent" in a new
  session's prompt renames it, before it owns anything, to <parent>-B<n> or <parent>-I<n> (store.make_child; the
  kind is stored as sessions[sid].kind; the first id stays an alias, resolved by `sg`, so $SG_SESSION_ID keeps
  working) and links it. A line without the kind makes no child. store._kind_allows keeps the kinds apart: a
  bound child opens and takes only subtasks of work owned (now or before) by a session above it; an independent
  one never takes that work or its subtasks. A session linked by claiming a hand-off has no kind: a continuation.
  A permanent lock (context, finish, break) is mirrored as sessions[sid].stopped in the store (it blocks nothing).
  The parent's hand-off waits for its bound children (`sg handoff children` lists them and the message to send;
  store.handoff refuses until their hand-offs exist, or --children-gone with the user's words); a bound child
  gets "PARENT SESSION ... HANDS OFF", writes its hand-offs and is locked. An independent child gets "PARENT
  SESSION ... STOPPED" once: make the work hand-off-ready, then go on. Each session is its own process (measured:
  one process record per session), so a parent's hook block cannot stop a child.
  UNVERIFIED (not in the desktop docs): whether a child's report reaches a locked parent.
- Compaction (measured 2026-11-04 on a real transcript: auto compaction 345k -> 22.5k, the next measurement 94k):
  the context is the last main reply's input usage, so a compacted conversation is measured as it is now.
  SessionStart source "compact" (documented) re-arms the one-time notices and adds a COMPACTED note; source "fork"
  with a record found through shared message ids gets a new SESSION-ID linked to the original and keeps its mode,
  notices and locks (under v1.4 a long-mode conversation continued under a new session id started in normal
  mode and was hard-stopped at 558k; how the app created that id is not known).
- New work check: `sg task check --title ... --title ...` once per new work request (bulk prompt or new tasks):
  limits, every running / urgent / queued task and idea in full, finished work by shared keywords (5-letter stems,
  so Turkish endings match; 2.0 tasks from the store, pre-2.0 PRE20-DONE lines from COMPLETED_TASKS.md), near
  copies by wording. Claude judges overlap, conflict and contradiction and explains them to the user. The archive
  is not part of it: searched only when needed, 2.0 entries by id, pre-2.0 entries by keywords.
- Merged side branches: at session start (cloud, or a project using side branches) `git ls-remote` lists sg/*
  branches contained in the remote default branch; the user is told (systemMessage) every session until they are
  deleted, with the "Automatically delete head branches" tip (GitHub docs: admin setting under Settings > General >
  Pull Requests; applies after pull request merges; a deleted head branch can be restored from the closed pull
  request). Only commits present in the checkout can be compared.
- Migration (revised 2026-11-04): files that only listed tasks are deleted whole, without a copy; task text inside
  other files is cut out ("remove_text"); imported items get pre-2.0 ids (PRE20-DONE-xxx, PRE20-IDEA-xxx; tasks keep
  TASK ids, marked "pre-2.0", because task ids form one sequence); the rest of the limits goes to "not_carried".
- Signatures: every file of the plugin carries "SESSION-GUARD: 2.0" (JSON in a description or "_signature"), checked
  by `sg manifest verify`; so do the files and records Session Guard writes (store, setup marker, config, migration
  checkpoint, session records, quota readings, archive and completed headers, hook and status line commands, the
  rollback tag message, commit messages).
- STATUS hygiene: text outside the generated block is tidied on every render and after every edit of STATUS.md
  (empty bullets, runs of empty lines and gaps inside a list removed, trailing spaces cut): an agent that deletes
  lines by number to keep the other numbers leaves exactly these. STATUS is edited by section.
Rollback of a migrated git project: `git reset --hard sg2-pre-migration-<date>` after saving later work.
