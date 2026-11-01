# Installing Session Guard on a computer

Instructions for Claude, when the user asks to install Session Guard from https://github.com/ocpipun/session-guard
on their computer (desktop app or terminal). Everything happens in this session; restarting the app is the last
step. After the restart the user types nothing and is asked nothing.

0. Control project: install and updates run only in a Claude project folder named exactly
   `session-guard-executive`. If this session's project folder has another name, tell the user (in their language)
   to create a folder named `session-guard-executive`, open it in Claude and paste the install line there; stop.
   Without this folder Session Guard stays off on the computer.
1. Get the files: `claude plugin marketplace add ocpipun/session-guard` (if it is already added, e.g. an older
   Session Guard is installed: `claude plugin marketplace update session-guard`). Nothing is removed by hand: an
   older version is updated in step 5. If `claude` is not on the PATH (the desktop app does not add it), run the
   same commands with the program in `$CLAUDE_CODE_EXECPATH` (the app's own Claude Code); this applies to every
   `claude` command below.
2. `SG` = `python "~/.claude/plugins/marketplaces/session-guard/plugins/session-guard/scripts/session_guard.py"`
   (`python3` on macOS/Linux; expand `~`). The plugin folder is the one above `scripts/`.
3. Run "Full setup" from `<plugin folder>/skills/setup/SKILL.md` now, in this session, including "Notices": the scan,
   one numbered list and one approval question; when `SG --set-language` cannot read the app language, the
   language question goes into the same message. Leftovers of an older Session Guard (rules in CLAUDE.md files,
   scripts, hooks) are conflicts like any other and go into the list.
4. The user declines: `SG --setup declined`; nothing is installed (an older version stays as it is). Stop here.
5. The user approves: do step 5 of the skill (changes, project files, global block, status line, notices,
   `SG --set-executive`, `SG --setup approved --all`, `SG --claim` and `SG --push` for each git project). Then, as the very last command: `claude plugin install session-guard@session-guard`,
   or `claude plugin update session-guard@session-guard` if an older version is installed (it applies at the
   restart, so open sessions keep working until then). If neither `claude` nor `$CLAUDE_CODE_EXECPATH` works, ask the
   user to run that command in a terminal.
6. Last message, in the user's language: Session Guard is installed and set up. Fully quit Claude (in the desktop
   app also from the system tray) and reopen it. Until then the open sessions run as before. After
   reopening, nothing needs to be typed: every session runs Session Guard from its start.
