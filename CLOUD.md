# Installing Session Guard from a cloud session

Instructions for Claude, when the user asks in a cloud session (claude.ai/code) to install Session Guard from
https://github.com/ocpipun/session-guard into the current repository. No local computer or plugin is needed.
Every change goes into the repository. The user's one command is the approval for the install: do every step
below in this one turn, without questions. Once the hooks are copied, Claude Code may load them into this
session; from then on this session's prompts are blocked ("open a new session"), so a question would never get
its answer here.

1. Download, if `/tmp/session-guard` is not there yet: `git clone --depth 1 https://github.com/ocpipun/session-guard /tmp/session-guard`.
   If the network blocks GitHub, tell the user the environment's network access must allow github.com, and stop.
2. `REPO` = the repository root. Write the hand-off note now, before the copy (format: "Hand-off" in
   `/tmp/session-guard/plugins/session-guard/skills/session-guard/SKILL.md`, in the user's language): what this
   session was doing, if anything, and as the next step "Session Guard project review (starts by itself)".
   `docs/STATUS.md` missing: create it with only that section. Present: replace only its hand-off section
   ("## Hand-off note" or the same heading in the user's language), leave the rest as it is.
3. Install: `python3 /tmp/session-guard/plugins/session-guard/scripts/cloud_setup.py "$REPO"`. It copies the script,
   hooks and skills into `.claude/` (existing settings are kept). The project review, CLAUDE.md and the other
   project files come in the next session.
4. Commit only the install and docs/STATUS.md, as one commit ("Install Session Guard"), and put it on the
   repository's default branch (every new cloud session starts from it):
   - Default branch: `git remote show origin | sed -n 's/.*HEAD branch: //p'` (call it `MAIN`); `git fetch origin "$MAIN"`.
   - If the commit sits directly on `origin/$MAIN` (`git rev-parse HEAD~1` equals `git rev-parse origin/$MAIN`):
     `git push origin HEAD:"$MAIN"`. Otherwise (the session branch has other work, or `MAIN` moved):
     `git checkout -b sg-install origin/"$MAIN"`, `git cherry-pick <install commit>`,
     `git push origin sg-install:"$MAIN"`, then go back to the session branch.
   - If GitHub rejects the push (branch protection), push the session branch and tell the user to open the pull
     request with "Create PR" and merge it; until it is merged, new sessions do not have Session Guard.
5. Tell the user, in their language: Session Guard is installed for this repository. Open a new session of this
   repository: there Session Guard starts its project review by itself (rules that conflict with it, similar
   files, the project files) and stays off until the user approves. This session and other sessions that were
   already open stay unprotected. There is no quota protection in the cloud (no hook can read the quota there).

To update later: the same steps again.
