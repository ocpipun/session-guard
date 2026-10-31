---
name: setup
description: Session Guard one-time setup and project review - finds rules, skills, scripts and hooks that conflict with Session Guard, removes them with the user's approval and installs the general rules block in the global CLAUDE.md. Use when a "[Session Guard]" message says INACTIVE and the user agrees, or when the user asks to set up or review Session Guard.
---

# Session Guard setup

Session Guard stays inactive until this is done. Never record "approved" without the user's explicit yes in this
conversation; never delete or change anything before that yes.

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

## Full setup (first time, or after the rules changed)
1. `SG --scan`: the global `~/.claude` items and every project Claude Code has sessions for, with matching lines.
2. Read every listed CLAUDE.md and memory file in full, and every listed skill, command, agent, script, output
   style, rule file or hook whose name or
   matching lines touch the topics above.
3. Show the user one numbered list: file and line, the text in short, why it conflicts, what you will do (delete
   the line, section, file or folder; or rewrite a line so it only points to Session Guard). Also say: the global
   CLAUDE.md gets the Session Guard block (created if missing; the user's other rules stay); approved items are
   deleted for good, with no backup (the user's decision); without approval Session Guard stays off.
4. Ask one question: approve the list? The user may say an item is not a conflict: drop it. If the user keeps an
   item you still consider a conflict, explain once why; if they still keep it, that is a decline.
5. Yes: delete or rewrite exactly the approved items, nothing else (no backup copies, no moving), `SG --install-global`
   (if it reports damaged markers, remove the broken `session-guard` marker lines by hand and run it again),
   `SG --setup approved --all`. Then `SG --print-rules` and follow those rules from now on in this session.
   Tell the user what was deleted or changed.
6. No: change nothing; `SG --setup declined`. Tell the user Session Guard stays off and `/session-guard:setup`
   runs this again.

## Project review (one project; message says INACTIVE in this project)
Same steps for that folder only: `SG --scan "<folder>"`, read, list, ask. Yes: the changes,
`SG --setup approved "<folder>"`, then `SG --print-rules` and follow them. No: `SG --setup declined "<folder>"`
(Session Guard stays off in that project only).
In a repository copy (`--project`, cloud): commit the changes and `.claude/session-guard/setup.json` with the
session's work, or the next cloud session asks again.
