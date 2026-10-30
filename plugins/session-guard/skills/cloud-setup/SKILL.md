---
name: cloud-setup
description: Copies Session Guard into the current project's repository so it also runs in cloud sessions (claude.ai/code). Use when the user asks to enable Session Guard for the cloud or for a repository.
disable-model-invocation: true
---

# Session Guard cloud setup

Cloud sessions do not install plugins; they run the repository's own `.claude/settings.json` hooks and
`.claude/skills/`. This copies Session Guard there, once per repository.

1. Find `cloud_setup.py` of this plugin: `${CLAUDE_PLUGIN_ROOT}/scripts/cloud_setup.py`. If that path was not
   filled in, search `~/.claude/plugins` for `session-guard/scripts/cloud_setup.py`.
2. Run it with the project root as argument: `python "<path>" "<project root>"` (`python3` on macOS/Linux).
3. Show the user its output and the changed files (`git status`).
4. Ask before committing. After the commit is pushed, cloud sessions of this repository run Session Guard.
   On a machine with the plugin installed, the project copy stays quiet, so the guard never runs twice.

To remove it from a repository: delete `.claude/session-guard/`, `.claude/skills/session-guard/` and the three
hooks whose command contains `session-guard/session_guard.py` from `.claude/settings.json`, then commit.
