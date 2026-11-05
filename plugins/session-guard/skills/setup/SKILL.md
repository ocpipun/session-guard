---
name: setup
description: Session Guard 2.0 install, conflict review with 1.x, project migration and notices. Use when a "[Session Guard]" message says SETUP PENDING, MIGRATION PENDING, INSTALLATION PAUSED or CONTINUE INSTALLATION, or when the user asks to install, update, migrate or uninstall Session Guard.
---
<!-- SESSION-GUARD: 2.0 | component: skill-setup | version: 2.0.0 -->

# Session Guard 2.0 setup

Runs only in the control project `session-guard-executive`. Never change or delete anything before the user's
explicit yes in this conversation; every push needs its own yes.

## Install / update from 1.x
1. `sg manifest verify` must say OK (a changed or unsigned file stops the install). `sg install conflicts`: the
   table of what belongs to 1.x and what 2.0 replaces.
2. Show the user one numbered list: the table, the 2.0 global block in `~/.claude/CLAUDE.md` (the 1.x block is
   removed only after the 2.0 block is written), every rule of `~/.claude/CLAUDE.md` outside Session Guard's
   blocks that contradicts or overlaps a Session Guard rule (it is removed: Session Guard's rule stays, user
   decision), and what migration will do (below). One approval question.
3. Yes: `sg install approve --executive "<this folder>"`, "Notices", then "Migration". The plugin itself is
   updated last (`claude plugin update session-guard@session-guard`; the program in `$CLAUDE_CODE_EXECPATH` when
   `claude` is not on the PATH); then the user fully restarts Claude. 1.x and 2.0 never guard at once: while a 1.x
   plugin is installed, 2.0 stays passive.

## Migration (one project at a time; the project's current, checked state is the source)
1. `sg migrate discover` (`--add <folder>` for projects found elsewhere); `sg migrate status` shows the list.
2. Per project: `sg migrate survey "<folder>"`. Give a subagent the survey JSON and have it read the files it
   names (STATUS and hand-off files, task lists, README, CLAUDE.md, AGENTS.md, GEMINI.md, skills, hooks, scripts,
   recent commits) by content, not by name. `task_list_files` are files that list tasks whatever their name
   (checkboxes, "To do / Pending / Done" headings): check the state of every item. `duplicates` are CLAUDE.md
   lines that repeat a status or task line. If an old session of that project still runs and is reachable, ask it
   for its hand-off instead. The report: current goal, finished work, open work, contradictions, unknowns, and
   which CLAUDE.md rules a script or hook could check. Do not rebuild old sessions' hand-offs one by one.
3. Write the plan JSON (`sg migrate plan-help`): at most 10 active tasks, 50 queued tasks, 50 ideas; finished
   items as "completed" (they get PRE20-DONE-xxx ids, ideas PRE20-IDEA-xxx, imported tasks are marked pre-2.0);
   what does not fit in "not_carried" (archived, shown to the user). Files that ONLY listed tasks go into
   "retire" (deleted whole, no copy); task text inside files used for other things too goes into "remove_text"
   (exact text cut out); CLAUDE.md lines that are state or tasks, not rules, into "claude_md_remove" (a rule lives
   only in CLAUDE.md, a task only in Session Guard's files); script candidates into "rule_scripts" (ideas linked
   to their rule); every rule of any instruction file that contradicts or overlaps a Session Guard rule into
   "sg_conflicts" (removed; Session Guard's rule stays). The old lists must be gone after the migration, so no agent follows them instead of Session
   Guard. Contradictions and unknowns listed, never settled by guessing. Show the user the plan, every file to
   delete and every text to cut, by name; on their yes `sg migrate apply "<folder>" --plan <file>` (git: rollback
   tag; a project without git has NO git rollback, only copies of edited files: say so), then `sg migrate verify
   "<folder>"`.
4. Git projects, on the user's yes: `sg migrate push "<folder>" --approval "<their words>"`. Only Session Guard's
   paths are committed; "NOT VERIFIED" means not done.
5. Old sessions of a migrated project are locked at their next prompt. Cloud sessions opened before cannot be
   locked from the computer (no documented API): tell the user to archive them at claude.ai/code.
6. Every project verified or skipped (`sg migrate skip "<folder>" --reason ...`): `sg migrate finish`.
Budget: at the 90% notice finish the current step, `sg migrate pause --reason budget`; the user continues in a
new session of this folder with "continue installation".

## Notices
`sg install language` reads the app language once (else ask, in English, word for word: "Which language would you
like session-guard notices in?" and run `sg install language <code>`). Missing translations: a JSON {name: text}
keeping every `%(name)s` / `%(name)d`, `sg install messages <file>`, then delete the file.

## Uninstall (on request; every step needs the user's yes, pushes their own yes)
Computer, from `session-guard-executive` (run the `sg` steps before the plugin is removed):
1. `sg migrate status`: the projects on 2.0. For each, `sg task list --root "<folder>"`: tell the user which open
   tasks and hand-offs exist; finish or hand off what matters first.
2. Per project, the user chooses: keep its Session Guard files as plain history (default) or remove them too.
   `sg install remove-project "<folder>"` keeps STATUS.md's last task list as plain text, the archive, backlog
   and completed list, and removes the computer guard hook, Session Guard's copy, skills copies and setup marker;
   `--remove-store` also removes `.claude/session-guard2/` (the task store). Git projects: commit those changes;
   push only with the user's yes.
3. `sg install uninstall` removes the 2.0 (and any 1.x) block of `~/.claude/CLAUDE.md` and the status line;
   `--purge-state` also deletes `~/.claude/session_guard_v2/` (session records, quota readings, migration
   checkpoint, backups: say so before).
4. `claude plugin uninstall session-guard@session-guard` (optionally `claude plugin marketplace remove
   session-guard`); the user fully quits Claude and reopens it. Old locks end with the plugin.
5. Write one line in the user's PC maintenance log if they keep one.
Cloud repository (from a cloud session of it, or a computer clone): `sg install remove-project "$REPO"` (same
choices), commit, push or pull request with the user's yes. Warn the user, in their language: Side branches `sg/<task-id>` hold the project's own unfinished work (its code), not Session Guard's files. Git never deletes a branch when it is merged (GitHub can, for a merged pull request, when the repository setting "Automatically delete head branches" is on), and a cloud session cannot delete branches at all (its GitHub proxy refuses deletions). Delete merged side branches by hand on GitHub.
Old cloud sessions: archive them at claude.ai/code.
