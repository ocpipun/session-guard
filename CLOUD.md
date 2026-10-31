# Installing Session Guard from a cloud session

Instructions for Claude, when the user asks in a cloud session (claude.ai/code) to install Session Guard from
https://github.com/ocpipun/session-guard into the current repository. No local computer or plugin is needed.
Every change goes into the repository.

1. Download, if `/tmp/session-guard` is not there yet: `git clone --depth 1 https://github.com/ocpipun/session-guard /tmp/session-guard`.
   If the network blocks GitHub, tell the user the environment's network access must allow github.com, and stop.
2. Set `SG` = `python3 /tmp/session-guard/plugins/session-guard/scripts/session_guard.py --project` and `REPO` = the
   repository root.
3. Project review: follow "What counts as a conflict" and "Project review" in
   `/tmp/session-guard/plugins/session-guard/skills/setup/SKILL.md` for `REPO` (`$SG --scan "$REPO"`, read, one
   numbered list, one question).
   - The user declines: change nothing, install nothing. Tell the user Session Guard is not installed, because it
     only works where no conflicting rule is left.
   - The user approves: make the changes, then continue.
4. Install: `python3 /tmp/session-guard/plugins/session-guard/scripts/cloud_setup.py --reviewed "$REPO"`.
   It copies the script, hooks and skills into `.claude/`, records the approval in `.claude/session-guard/setup.json`
   and creates the missing CLAUDE.md, docs/STATUS.md and docs/STATUS_ARCHIVE.md (existing files stay as they are).
5. Show the user the output and `git status`. Ask before committing and pushing.
6. Tell the user: Session Guard surely works from the next session of this repository on (hooks are read at
   session start), and the global rules come from the project copy, because the cloud has no global CLAUDE.md.

To update later: the same steps again.
