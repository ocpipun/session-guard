# Installing Session Guard on a computer

Instructions for Claude, when the user asks to install Session Guard from https://github.com/ocpipun/session-guard
on their computer (desktop app or terminal). Everything happens in this session; restarting the app is the last
step. After the restart the user types nothing and is asked nothing.

1. Install the plugin: `claude plugin marketplace add ocpipun/session-guard` (if it is already added:
   `claude plugin marketplace update session-guard`), then `claude plugin install session-guard@session-guard`.
   If the `claude` command is not available, ask the user to type `/plugin marketplace add ocpipun/session-guard`
   and `/plugin install session-guard@session-guard`, then continue.
2. Find the installed script: search `~/.claude/plugins/cache` for `session-guard/scripts/session_guard.py` (the
   newest version folder). `SG` = `python "<that path>"` (`python3` on macOS/Linux). The plugin folder is the one
   above `scripts/`.
3. Run "Full setup" from `<plugin folder>/skills/setup/SKILL.md` now, in this session, including "Notices": the scan,
   one numbered list and one approval question; when `SG --set-language` cannot read the app language, the
   language question goes into the same message. Session Guard's hooks may or may not run in this session yet;
   either way follow the skill.
4. The user declines: `SG --setup declined`; Session Guard stays off (offer to uninstall the plugin). Stop here.
5. The user approves: do step 5 of the skill (changes, project files, global block, status line, notices,
   `SG --setup approved --all`). From now on every session that started before the install is locked until the
   restart (where Session Guard's hooks already run in it).
6. Last message, in the user's language: Session Guard is installed and set up. Fully quit Claude (in the desktop
   app also from the system tray) and reopen it. Until then the open sessions are unprotected or locked. After
   reopening, nothing needs to be typed: every session runs Session Guard from its start.
