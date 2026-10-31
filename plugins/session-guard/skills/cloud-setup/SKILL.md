---
name: cloud-setup
description: Copies Session Guard into the current project's repository so it also runs in cloud sessions (claude.ai/code). Use when the user asks to enable Session Guard for the cloud or for a repository.
disable-model-invocation: true
---

# Session Guard cloud setup

Cloud sessions do not install plugins; they run the repository's own `.claude/settings.json` hooks and
`.claude/skills/`. There is no global CLAUDE.md in the cloud: the project copy gives the general rules at session
start. This copies Session Guard into the repository, once per repository.

1. The repository must have passed the project review: if the "[Session Guard]" message says INACTIVE for this
   project, or you are unsure, run skill session-guard:setup "Project review" for the project root first. The
   script refuses to copy into a repository that was not reviewed.
2. Find `cloud_setup.py` of this plugin: `${CLAUDE_PLUGIN_ROOT}/scripts/cloud_setup.py`. If that path was not
   filled in, search `~/.claude/plugins` for `session-guard/scripts/cloud_setup.py`.
3. Run it with the project root as argument: `python "<path>" "<project root>"` (`python3` on macOS/Linux).
   It also creates the missing CLAUDE.md, docs/STATUS.md and docs/STATUS_ARCHIVE.md; existing files stay as they are.
4. Show the user its output and the changed files (`git status`).
5. Ask before committing. After the commit is pushed, cloud sessions of this repository run Session Guard.
   On a machine with the plugin installed, the project copy stays quiet, so the guard never runs twice.
   If the repository's CLAUDE.md, skills or hooks change later in a way the cloud copy finds unreviewed, the
   cloud session asks for a review (skill session-guard-setup) before Session Guard works there.

To remove it from a repository: delete `.claude/session-guard/`, `.claude/skills/session-guard/`,
`.claude/skills/session-guard-setup/` and the three hooks whose command contains `session-guard/session_guard.py`
from `.claude/settings.json`, then commit.
