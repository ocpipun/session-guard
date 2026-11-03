<!-- SESSION-GUARD: 2.0 | component: doc-install | version: 2.0.0 -->
# Installing or updating Session Guard 2.0 on a computer

Instructions for Claude, when the user asks to install or update Session Guard from
https://github.com/ocpipun/session-guard. Every step that changes something needs the user's yes in this
conversation; pushes need their own yes.

0. Control project: only in a Claude project folder named exactly `session-guard-executive`. Otherwise tell the
   user (in their language) to create it, open it in Claude and paste the install line there; stop.
1. Files: `claude plugin marketplace add ocpipun/session-guard` (already added: `claude plugin marketplace update
   session-guard`). If `claude` is not on the PATH, use the program in `$CLAUDE_CODE_EXECPATH`.
2. `SG` = `python "~/.claude/plugins/marketplaces/session-guard/plugins/session-guard/scripts/session_guard.py"`
   (`python3` on macOS/Linux; expand `~`).
3. Follow skill `setup` (`<plugin folder>/skills/setup/SKILL.md`): "Install / update from 1.x" (manifest check,
   conflict table, one approval), "Notices", then "Migration", one project at a time.
4. Last command: `claude plugin install session-guard@session-guard`, or `claude plugin update
   session-guard@session-guard` over 1.x. While 1.x is installed 2.0 stays passive, so 1.x and 2.0 never guard the
   same session.
5. Last message, in the user's language: fully quit Claude (tray included) and reopen it. If the token budget
   paused the installation, a new session of `session-guard-executive` continues it with "continue installation".

Uninstall: skill `setup`, "Uninstall".
