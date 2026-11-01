# Session Guard

**A Claude Code plugin that measures how big your session has grown, how long you were away and how much quota is left, hands work off cleanly before the session gets expensive, and locks finished sessions so nothing you type later can mix things up.**

[Türkçe](README.tr.md) · Version 1.2

---

## Why

A Claude Code session gets more expensive with every message: each new prompt re-sends the whole conversation (the *context*). Past a few hundred thousand tokens, one prompt can cost as much as a whole short session. Three things make this worse:

1. **Claude cannot see its own context size.** The app shows it to you, but the model never receives that number, so it cannot warn you or stop on its own. Rules like "hand off at 300k" in a `CLAUDE.md` file are guesses at best.
2. **Long breaks are expensive.** The prompt cache expires (one hour on a subscription). The first prompt after a long break re-processes the whole context at full price.
3. **The quota runs out mid-step.** When the 5-hour or weekly limit is hit, work stops wherever it is.

There is also a quieter problem: after you have written a hand-off note and moved to a new session, typing into the old one again creates two diverging versions of the same work.

Session Guard fixes all of this with measurements instead of guesses.

## What it does

| Situation | Normal mode | Long mode |
|---|---|---|
| 250k tokens | Notice | Notice |
| 300k | **Hand-off**: STATUS.md is cleaned up, finished items go to the archive, a hand-off note is written. After that: no new tasks, only questions | Notice only |
| 350k | Target stop: open a new session | — |
| 400k | **Hard stop**, session locked | — |
| 700k | — | Notice; work is made hand-off ready (Claude may keep working) |
| 800k | — | No new work; only simple questions; final hand-off note |
| 900k | — | **Hard stop**, session locked |
| Break over 1 hour, context > 100k | Your prompt is **not** run. Claude writes the hand-off note, copies your prompt into it word for word, and the session locks | Break rule off |
| Break over 1 hour, context ≤ 100k | One-line reminder, then your prompt runs | Break rule off |
| Quota 90% (5-hour) or 98% (weekly) | No new task; current work made hand-over ready within 15 minutes; locked until the reset | same |
| `finish session` | Hand-off + usage measurement, then the session locks | same |

- **Thresholds only on Session Guard's word.** Claude never acts on a threshold early: no early notice, no early hand-off, no "open a new session" because it expects the next task to be big. Session Guard checks after every tool call while Claude works and stops at 400k, so the plan above always holds. Each threshold reaches you in one fixed form, for example: "250k threshold passed: notice only. *This is a Session Guard notice."
- **The break rule only counts *your* absence.** If Claude started a background job (a render, a long script, a subagent) and is waiting for it, the gap is not a break.
- **A locked session never passes a prompt to Claude.** The prompt is blocked before it is sent, so it costs nothing and changes nothing. Locks are kept apart by kind: context limit, finish session, break, quota (lifts at the reset) and restart (a session that started before the install).
- **Reopened conversations stay locked.** The desktop app can reopen an old conversation under a new session id. Session Guard recognises it by its message ids, so the lock (and long mode) survive.
- **Session start:** Claude gets the rules and your `docs/STATUS.md` automatically. You never type "read STATUS.md" again; the archive (`docs/STATUS_ARCHIVE.md`) is only searched when needed.
- **Reply style that saves tokens:** plain words, short sentences, each issue numbered, with only the labels it needs: PROBLEM / RECOMMENDATION / ASSUMPTION / WARNING / UNVERIFIED (in your language). If you want more detail, ask.
- **Facts stay facts.** Claude never presents something it has not researched or is not sure of as fact (it is labelled UNVERIFIED), and marks its own recommendations and assumptions as its own.
- **Project files.** Every project gets `CLAUDE.md`, `docs/STATUS.md` and `docs/STATUS_ARCHIVE.md`. Files that already exist are never overwritten. Files that do the same job under another name (AGENTS.md, GEMINI.md, .cursorrules, HANDOFF*, NOTES*, TODO*, PROGRESS*, status files and the like) are reviewed with your approval: conflicting lines go, useful content moves into STATUS / ARCHIVE. Nothing is created in your home folder, a drive root or system folders, and a git worktree uses its main checkout's files.
- **One rule set.** Session Guard's general rules live in a marked block of your global `~/.claude/CLAUDE.md`. Rules, skills, scripts and hooks that conflict with them are removed during setup, with your approval.
- **Notices in your language.** Claude talks to you in your language. The notices the app shows directly (locks, blocked messages, thresholds) come in the language selected in the Claude desktop app's settings at setup time (read once; Turkish ships with the plugin, other languages are translated by Claude during setup). Where it cannot be read, Claude asks: "Which language would you like session-guard notices in?" It is never checked again; for another language later, ask Claude to add it. In the terminal they are English.

## Quota protection (Session Follow, computer only)

- **Terminal:** read from Session Guard's status line, at zero token cost (only if you have no other status line).
- **Desktop app:** status lines do not run there, so Claude reads the app's usage tool when the last reading is older than 20 minutes (with every prompt from 85% 5-hour or 95% weekly; during long work at most every 5 minutes). About 400 tokens per reading (estimate).
- **At 90% (5-hour) or 98% (weekly)**, when the reset is at least 45 minutes away: Claude starts no new task, brings the current work to a hand-over-ready state within 15 minutes and writes one line into STATUS.md ("stopped at ..."). Then the session locks until the reset. Sessions started meanwhile are locked at once. A reset closer than 45 minutes: work goes on.
- **After the reset** the lock clears by itself. If you were away for over an hour in normal mode, the break rule applies as usual.
- One reading serves every session on the computer (the quota belongs to the account).
- **Not in the cloud:** no hook can read the quota in a cloud session (checked against the documentation and tested). Cloud sessions keep every other rule.

## Commands

Type them at the **end** of a message, or at the **start** followed by a comma or period. Anywhere else (inside a sentence, a quote, pasted text) they are ignored on purpose.

| Command | Effect |
|---|---|
| `start long mode` | Long mode for this session only. It cannot be switched off; a new session starts in normal mode. Use it for long, complex or unattended work. |
| `finish session` | Ends the topic: hand-off note + usage measurement, then the session locks. Use it whenever one topic is done, so one session never mixes several topics. |

Examples: `Start long mode, render the remaining clips` · `That's all for today, finish session`

## How it works

Session Guard is a Claude Code **plugin**: a package of hooks, a script, skills and translations.

- **Hooks** run the script automatically on three events: session start, every prompt you submit, and after every tool Claude uses. A fourth, background hook is the 24-hour watch.
- **The script** (`scripts/session_guard.py`, Python, standard library only) reads the session transcript that Claude Code already keeps on your disk (`~/.claude/projects/...`). The context size is taken from the token usage of Claude's last reply; the break is the time since that reply. It then:
  - at a threshold (or a break, long mode, finish session, quota stop) adds an instruction with the measured numbers to Claude's context; a normal prompt gets nothing, so it costs no tokens,
  - shows you a short notice when a threshold is first passed,
  - blocks prompts in a locked session,
  - stops Claude's turn if a hard limit is reached in the middle of work.
- **Skills** hold the procedures (setup and project review, hand-off, break hand-off, finish session). Claude loads them only when needed.
- **State** is Session Guard's own small record in `~/.claude/session_guard/`: per session long mode, locks and announced thresholds; the shared quota readings (`session_follow/`); the setup record, notice language and your own translations (`config/`). No project file or conversation is ever in it. Session records unused for 30 days are deleted; a locked session's record is kept, so an old session stays locked even when reopened months later.

Nothing is sent anywhere. The script only reads local files and writes its own files.

## Install (computer)

Requirements: Claude Code (desktop app or terminal), Python 3 (`python` on Windows, `python3` on macOS/Linux). On Windows, Claude Code should use Git Bash for hooks (the default when Git for Windows is installed).

Paste this one line into Claude Code:

```
Install Session Guard: run "claude plugin marketplace add ocpipun/session-guard", then follow INSTALL.md in ~/.claude/plugins/marketplaces/session-guard
```

What happens, all in that one session:

1. Claude downloads Session Guard. An older version needs no removal: it is updated at the end.
2. It scans your global `~/.claude` (CLAUDE.md, memory files, skills, commands, agents, scripts, output styles, rules, hooks) and every project Claude Code has sessions for, and shows **one list**: rules that conflict with Session Guard or repeat it, skills, scripts or hooks that do the same job, and files that do the project files' job under another name. Project facts and a project's own STATUS layout are not conflicts.
3. The same message says what else setup does: the general rules block in `~/.claude/CLAUDE.md` (your other rules stay), the missing project files, and the status line in `~/.claude/settings.json` (terminal quota readings; another status line you already have is left alone; the code is public here, MIT). If the app language cannot be read, the language question is in it too.
4. You answer **once**. The approved items are deleted for good (no backup copies, so nothing half-moved is left behind). **If you do not approve, Session Guard stays off;** `/session-guard:setup` runs it again any time.
5. Claude installs (or updates) the plugin. Last step: **fully quit Claude** (in the desktop app also from the system tray) **and reopen it.** Until then, open sessions run as before. After reopening you type nothing and are asked nothing.

Installed at user scope, it works in every project on that machine. A project opened later that has its own CLAUDE.md, similar files, skills or hooks gets the same review once, for that project only (it starts by itself); declining turns Session Guard off in that project only. When an update changes the rules, the setup runs again.

## Cloud sessions (claude.ai/code)

Cloud sessions do not install plugins. Session Guard is installed into each repository once, from a cloud session of that repository, with this one line:

```
Install Session Guard: git clone --depth 1 https://github.com/ocpipun/session-guard /tmp/session-guard, then follow /tmp/session-guard/CLOUD.md
```

Claude shows the repository's review list and asks "Which language would you like session-guard notices in?" (the cloud cannot read the app language) and one approval question. If you decline, nothing is installed. If you approve, Claude installs Session Guard into the repository's `.claude/` folder, creates the missing project files and commits everything straight to the repository's main branch. You go on in the same session: Session Guard runs there from your next message, and in every new session of that repository. Once per repository is enough. The cloud has no global CLAUDE.md, so the repository copy brings the general rules itself. On a computer where the plugin is installed, the repository copy stays quiet, so the guard never runs twice. **No quota protection in the cloud.**

## Uninstall

Ask Claude to uninstall Session Guard. It removes its status line (`session_guard.py --uninstall-statusline`), then runs:

```bash
claude plugin uninstall session-guard@session-guard
```

Then remove the block between `<!-- session-guard:begin` and `<!-- session-guard:end -->` from `~/.claude/CLAUDE.md`. For a repository set up for the cloud, ask Claude to remove `.claude/session-guard/`, `.claude/skills/session-guard/`, `.claude/skills/session-guard-setup/` and the Session Guard hooks from `.claude/settings.json`.

## Limits and honest notes

- The context number is the size of the *last reply*, so it can lag one step behind. Thresholds are checked again after every tool call while Claude works.
- A background job or subagent whose "finished" notice never arrives counts as running for at most 24 hours. After that, and when Claude has worked on one message for over 24 hours, Session Guard tells you it may be a bug and suggests `finish session` if it is stuck. A 24-hour timer starts with every prompt (a local check, not a Claude request, so it uses no quota); if 24 hours pass with no new prompt and the session is still busy, it wakes Claude, which tells you. In the cloud the timer dies if the cloud stops an inactive session first.
- The app language is read from the desktop app's own settings file (`config.json`, key `locale`). This file is not documented: on Windows it was measured, on macOS (`~/Library/Application Support/Claude/`) and Linux (`~/.config/Claude/`) it comes from the app's code and is not tested. If it is not found, Claude asks.
- Quota readings in the desktop app cost tokens (about 400 each, estimate); the terminal status line costs none.
- "Every project" means every project Claude Code has a session for (found from `~/.claude/projects`). Folders never opened in Claude Code are reviewed when first opened.
- Whether an item conflicts is decided by Claude and approved by you; the script only finds the candidates.
- Thresholds are fixed in the script (`NORMAL_STEPS`, `LONG_STEPS`, `STOP_AT`). They are working values, not proven quota savings.
- The v1.2 install flows have not yet been tested in a live install (computer or cloud).

## Tests

```bash
python -m unittest discover -s tests
```

## License

MIT
