# Installing Session Guard from a cloud session

Instructions for Claude, when the user asks in a cloud session (claude.ai/code) to install Session Guard from
https://github.com/ocpipun/session-guard into the current repository. No local computer or plugin is needed.
Every change goes into the repository. Everything happens in this session; no second session is needed.

1. Download, if `/tmp/session-guard` is not there yet: `git clone --depth 1 https://github.com/ocpipun/session-guard /tmp/session-guard`.
   If the network blocks GitHub, tell the user the environment's network access must allow github.com, and stop.
2. `SG` = `python3 /tmp/session-guard/plugins/session-guard/scripts/session_guard.py --project`, `REPO` = the
   repository root. Copy nothing yet.
3. Project review: follow "What counts as a conflict", "Similar files" and "Project review" in
   `/tmp/session-guard/plugins/session-guard/skills/setup/SKILL.md` for `REPO` (`$SG --scan "$REPO"`, read, one
   numbered list). In the same message, in English and word for word, ask:
   "Which language would you like session-guard notices in?" Then one approval question.
4. The user declines: change nothing, install nothing. Tell the user Session Guard is not installed, because it
   only works where no conflicting rule is left. Stop here.
5. The user approves: do all of the following in this one turn, without further questions:
   1. Make the approved changes.
   2. `python3 /tmp/session-guard/plugins/session-guard/scripts/cloud_setup.py --reviewed "$REPO"`: copies the
      script, hooks, skills and translations into `.claude/`, records the approval and creates the missing
      CLAUDE.md, docs/STATUS.md and docs/STATUS_ARCHIVE.md.
   3. Language: `cd "$REPO" && python3 .claude/session-guard/session_guard.py --project --set-language <code>`
      (`english` for none). If it reports notices without a translation, translate them as the skill's "Notices"
      says, with the same `--project` command and `--set-messages`.
   4. Commit all of it as one commit ("Install Session Guard") and put it on the repository's default branch
      (every new cloud session starts from it):
      - Default branch: `git remote show origin | sed -n 's/.*HEAD branch: //p'` (call it `MAIN`); `git fetch origin "$MAIN"`.
      - If the commit sits directly on `origin/$MAIN` (`git rev-parse HEAD~1` equals `git rev-parse origin/$MAIN`):
        `git push origin HEAD:"$MAIN"`. Otherwise: `git checkout -b sg-install origin/"$MAIN"`,
        `git cherry-pick <install commit>`, `git push origin sg-install:"$MAIN"`, then go back to the session branch.
      - If GitHub rejects the push (branch protection), push the session branch and tell the user to open the pull
        request with "Create PR" and merge it; until it is merged, new sessions do not have Session Guard.
6. Last message, in the user's language: Session Guard is installed for this repository and runs in this session
   from the next message on (it brings its rules and STATUS.md then), and in every new session of it. Other
   sessions that were already open stay unprotected. There is no quota protection in the cloud.

To update later: the same steps again.
