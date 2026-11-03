<!-- SESSION-GUARD: 2.0 | component: doc-cloud | version: 2.0.0 -->
# Installing Session Guard 2.0 from a cloud session

Instructions for Claude, when the user asks in a cloud session (claude.ai/code) to install Session Guard into the
current repository. The session must be in "Accept edits" mode (Auto mode blocks changes to hooks and CLAUDE.md
as self-modification, measured with 1.4).

1. `git clone --depth 1 https://github.com/ocpipun/session-guard /tmp/session-guard`. If GitHub is blocked, tell
   the user the environment must allow github.com; stop.
2. `SG` = `python3 /tmp/session-guard/plugins/session-guard/scripts/session_guard.py`, `REPO` = repository root.
   A computer project (its `.claude/settings.json` has a `session-guard2-home` or `session-guard-home` hook) is not
   installed into.
3. Survey and plan as skill `setup` "Migration" says, for `REPO` only (`$SG migrate discover --add "$REPO"`,
   `$SG migrate survey "$REPO"`, read, plan). Ask, in English and word for word, "Which language would you like
   session-guard notices in?", then one approval question.
4. Yes: `$SG install cloud "$REPO"` (repository copy in `.claude/session-guard2/bin/`, hooks, skills; a 1.x copy's
   hooks are replaced), then `$SG migrate apply "$REPO" --plan <file> --session <id>` with `"home": "cloud"` in the
   plan, `$SG migrate verify "$REPO"`. Remove a 1.x copy (`.claude/session-guard/`, `.claude/skills/session-guard*`
   of 1.x) only with the user's yes.
5. Commit everything as one commit and put it on the default branch (push, or a pull request the user merges when
   the branch is protected); until then new sessions do not have Session Guard.
6. Tell the user: Session Guard runs in every new cloud session of this repository; sessions opened before stay
   unprotected and cannot be locked from here (archive them at claude.ai/code). No quota protection in the cloud.
