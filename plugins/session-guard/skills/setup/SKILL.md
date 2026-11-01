---
name: setup
description: Session Guard one-time setup and project review - finds rules, similar files, skills, scripts and hooks that conflict with Session Guard, removes them with the user's approval, installs the general rules block and the terminal status line. Use when a "[Session Guard]" message says SETUP PENDING or PROJECT REVIEW PENDING, or when the user asks to set up or review Session Guard.
---

# Session Guard setup

Session Guard stays inactive until this is done. It starts by itself: a "[Session Guard] SETUP PENDING" or
"PROJECT REVIEW PENDING" message means run it now, without asking whether to. Never record "approved" without the
user's explicit yes in this conversation; never delete or change anything before that yes.

`SG` below is the script command from the "[Session Guard]" message (`python "<path>/session_guard.py"`, with
`--project` in a repository copy). Without a message: `${CLAUDE_PLUGIN_ROOT}/scripts/session_guard.py`; if that
was not filled in, search `~/.claude/plugins` for `session-guard/scripts/session_guard.py`.

## What counts as a conflict
Compare with the Session Guard rules (`SG --print-rules`). An item conflicts when it covers a topic those rules
cover and says something different, repeats it (a duplicate), or does Session Guard's job another way:
- context or token limits; when to hand off, stop or open a new session; breaks; long mode; ending or locking a session;
- reading STATUS at session start; the roles of CLAUDE.md, STATUS.md, STATUS_ARCHIVE.md; STATUS kept as a log;
  separate hand-off files; a second archive; "a project CLAUDE.md wins over the global one" on these topics;
- reply style (labels, length) other than Session Guard's; presenting guesses as facts;
- session measurement rules, scripts or hooks that duplicate `SG --report`;
- skills, commands, agents, scripts or hooks that measure context, track breaks, write hand-off notes or lock
  sessions (for example an older hand-made Session Guard copy).
Not a conflict: project facts and decisions; what a project's STATUS.md contains and how it looks (the rules allow
a project to set this); rules on other topics. When unsure, list the item as "unsure" and let the user decide.

## Similar files (every project)
The scan marks files that do the job of CLAUDE.md / STATUS / ARCHIVE under another name ("[same job ...]": AGENTS.md,
GEMINI.md, .cursorrules, .cursor/rules, .windsurfrules, .clinerules, .github/copilot-instructions.md, HANDOFF*,
NOTES*, TODO*, PROGRESS*, CONTEXT.md, MEMORY.md, any status file). Read each in full. In the list, say for each line
or part: delete (conflict or duplicate), move (current work, open issues, next steps go to docs/STATUS.md;
decisions, measurements, history go to docs/STATUS_ARCHIVE.md; permanent project facts go to CLAUDE.md) or keep.
A file emptied by moves is deleted. Another tool's own rule file (AGENTS.md and the like) is kept and only edited,
unless the user wants it gone.

## Notices (part of every approved full setup)
The app shows some Session Guard notices itself (blocked prompts, locks, thresholds); Claude never sees them. Their
language is chosen once, here, and never checked again: `SG --set-language` reads the Claude desktop app's language
setting one time. Terminal and cloud: English. If it reports notices without a translation, translate them into that
language, keep every `%(name)s` / `%(name)d` placeholder (`%%` is a literal percent sign) and leave "Session Guard",
"finish session" and "start long mode" untranslated; write a JSON object {name: text} to a temporary file,
`SG --set-messages <file>`, delete the file. Do not show the translations to the user.
Later, only when the user asks for another language: `SG --set-language <code>` (e.g. `fr`; `english` turns
translations off), then translate as above if notices are missing. Until translated they are English. A
session-start message "notices without a translation" (after an update) means: translate the listed ones, silently.

## Full setup (first time, or after the rules changed)
1. `SG --scan`: the global `~/.claude` items and every project Claude Code has sessions for, with matching lines.
2. Read every listed CLAUDE.md and memory file and every "[same job ...]" file in full, and every listed skill,
   command, agent, script, output style, rule file or hook whose name or matching lines touch the topics above.
3. Show the user one numbered list: file and line, the text in short, why it conflicts or where it moves, what you
   will do (delete the line, section, file or folder; rewrite a line so it only points to Session Guard; move it).
   Then say, in short:
   - the global CLAUDE.md gets the Session Guard block (created if missing; the user's other rules stay);
   - missing CLAUDE.md, docs/STATUS.md, docs/STATUS_ARCHIVE.md are created;
   - on a computer (not in a repository copy): Session Guard writes a status line command into
     `~/.claude/settings.json` (key `statusLine`). Why: in the terminal it gives Session Guard the 5-hour and weekly
     quota at zero token cost, so a session can stop before the quota runs out (the desktop app does not run status
     lines; there Claude reads the usage tool). If another status line is already set, it is left alone and the
     terminal gives no quota readings. The code is public on GitHub
     (https://github.com/ocpipun/session-guard), MIT license;
   - approved items are deleted for good, with no backup (the user's decision); without approval Session Guard
     stays off.
4. Ask one question: approve the list? The user may say an item is not a conflict: drop it. If the user keeps an
   item you still consider a conflict, explain once why; if they still keep it, that is a decline.
5. Yes: `SG --create-files "<project>"` for each project that gets moved content, then delete, rewrite or move
   exactly the approved items, nothing else (no backup copies). `SG --install-global` (if it reports damaged
   markers, remove the broken `session-guard` marker lines by hand and run it again), `SG --install-statusline`
   (not with `--project`), "Notices", `SG --setup approved --all`. Tell the user what was deleted, moved or changed, and that
   Session Guard is on from now on in every session (no restart needed). The rules arrive with the next message.
   Then handle the user's original message, if it is still open.
6. No: change nothing; `SG --setup declined`. Tell the user Session Guard stays off and `/session-guard:setup`
   runs this again.

## Project review (one project; message says PROJECT REVIEW PENDING)
Same steps for that folder only: `SG --scan "<folder>"`, read, list (the status line and global block are not part
of it), ask. Yes: `SG --create-files "<folder>"`, the changes, `SG --setup approved "<folder>"`. No:
`SG --setup declined "<folder>"` (Session Guard stays off in that project only).
In a repository copy (`--project`, cloud): commit the changes, the project files, and `.claude/session-guard/setup.json`
with the session's work, or the next cloud session asks again.

## Uninstall (on request)
`SG --uninstall-statusline`, then `claude plugin uninstall session-guard@session-guard`;
remove the block between `<!-- session-guard:begin` and `<!-- session-guard:end -->` from `~/.claude/CLAUDE.md`.
