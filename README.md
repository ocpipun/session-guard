# Session Guard

**A Claude Code plugin that measures how big your session has grown and how long you were away, hands work off cleanly before the session gets expensive, and locks finished sessions so nothing you type later can mix things up.**

[Türkçe](README.tr.md)

---

## Why

A Claude Code session gets more expensive with every message: each new prompt re-sends the whole conversation (the *context*). Past a few hundred thousand tokens, one prompt can cost as much as a whole short session. Two things make this worse:

1. **Claude cannot see its own context size.** The app shows it to you, but the model never receives that number, so it cannot warn you or stop on its own. Rules like "hand off at 300k" in a `CLAUDE.md` file are guesses at best.
2. **Long breaks are expensive.** The prompt cache expires (one hour on a subscription). The first prompt after a long break re-processes the whole context at full price.

There is also a quieter problem: after you have written a hand-off note and moved to a new session, typing into the old one again creates two diverging versions of the same work.

Session Guard fixes all three with measurements instead of guesses.

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
| `finish session` | Hand-off + usage measurement, then the session locks | same |

- **The break rule only counts *your* absence.** If Claude started a background job (a render, a long script, a subagent) and is waiting for it, the gap is not a break.
- **A locked session never passes a prompt to Claude.** The prompt is blocked before it is sent, so it costs nothing and changes nothing. You see "Session closed, open a new session". Locks are kept apart by kind: context limit, finish session, break, quota (lifts at the reset) and restart (a session that started before the install).
- **Reopened conversations stay locked.** The desktop app can reopen an old conversation under a new session id. Session Guard recognises it by its message ids, so the lock (and long mode) survive.
- **Session start:** Claude gets the rules and your `docs/STATUS.md` automatically. You never type "read STATUS.md" again; the archive (`docs/STATUS_ARCHIVE.md`) is only searched when needed.
- **Reply style that saves tokens:** plain words, short sentences, each issue numbered, with only the labels it needs: PROBLEM / RECOMMENDATION / ASSUMPTION / WARNING / UNVERIFIED (in your language). If you want more detail, ask.
- **Facts stay facts.** Claude never presents something it has not researched or is not sure of as fact (it is labelled UNVERIFIED), and marks its own recommendations and assumptions as its own.
- **Project files.** In every new project the first session creates `CLAUDE.md`, `docs/STATUS.md` and `docs/STATUS_ARCHIVE.md`. Files that already exist (in any letter case, or `.claude/CLAUDE.md`) are never recreated or overwritten. Nothing is created in your home folder, a drive root or system folders, and a git worktree uses its main checkout's files.
- **One rule set.** Session Guard's general rules live in a marked block of your global `~/.claude/CLAUDE.md`. Rules, skills, scripts and hooks that conflict with them are removed during the one-time setup, with your approval (see below).
- **Any language.** Commands are English; Claude talks to you in your language. The notices the app shows directly (locks, blocked messages, thresholds) are translated once during setup into the language you use with Claude; until then, and for the restart notice before setup, they are English.

## Quota protection (Session Follow, computer only)

Session Guard follows your **5-hour** and **weekly (all models)** quota, so a session stops cleanly before the quota runs out instead of in the middle of a step.

- **Terminal:** read from Session Guard's status line, at zero token cost (only if you have no other status line).
- **Desktop app:** status lines do not run there, so Claude reads the app's usage tool when the last reading is older than 20 minutes (with every prompt from 85% 5-hour or 95% weekly; during long work at most every 5 minutes). About 400 tokens per reading (estimate).
- **At 90% (5-hour) or 98% (weekly)**, when the reset is at least 45 minutes away: Claude starts no new task, brings the current work to a hand-over-ready state within 15 minutes and writes one line into STATUS.md ("stopped at ..."). Then the session locks until the reset: "Quota 91% (5-hour), resets at 14:10. Continue after that." Sessions started meanwhile are locked at once. A reset closer than 45 minutes: work goes on.
- **After the reset** the lock clears by itself. If you were away for over an hour in normal mode, the break rule applies as usual.
- One reading serves every session on the computer (the quota belongs to the account).
- **Not in the cloud:** no hook can read the quota in a cloud session (checked against the documentation and tested). Cloud sessions keep every other rule.

## Commands

Type them at the **end** of a message, or at the **start** followed by a comma or period. Anywhere else (inside a sentence, a quote, pasted text) they are ignored on purpose.

| Command | Effect |
|---|---|
| `start long mode` | Long mode for this session only. It cannot be switched off; a new session starts in normal mode. Use it for long, complex or unattended work (for example while you are away and follow along by remote control). |
| `finish session` | Ends the topic: hand-off note + usage measurement, then the session locks. Use it whenever one topic is done, so one session never mixes several topics. |

Examples: `Start long mode, render the remaining clips` · `That's all for today, finish session`

## How it works

Session Guard is a Claude Code **plugin**: a package of hooks, a script and skills.

- **Hooks** run the script automatically on three events: session start, every prompt you submit, and after every tool Claude uses.
- **The script** (`scripts/session_guard.py`, Python, standard library only) reads the session transcript that Claude Code already keeps on your disk (`~/.claude/projects/...`). The context size is taken from the token usage of Claude's last reply; the break is the time since that reply. It then:
  - at a threshold (or a break, long mode, finish session) adds an instruction with the measured numbers to Claude's context; a normal prompt gets nothing, so it costs no tokens,
  - shows you a short message when a threshold is first passed,
  - blocks prompts in a locked session,
  - stops Claude's turn if a hard limit is reached in the middle of work.
- **Skills** hold the procedures (setup and project review, hand-off, break hand-off, finish session). Claude loads them only when needed.
- **State** is Session Guard's own small record per session in `~/.claude/session_guard/`: is long mode on, is the session locked, which thresholds were already announced. It is not your work: no project file, conversation or Claude data is ever in it. Records unused for 30 days are deleted so the hook stays fast; a locked session's record is never deleted, so an old session stays locked even when reopened months later. The setup approval is kept in `~/.claude/session_guard/config/setup.json`.

Nothing is sent anywhere. The script only reads local files and writes its own state files.

## Install

Requirements: Claude Code, Python 3 (`python` on Windows, `python3` on macOS/Linux). On Windows, Claude Code should use Git Bash for hooks (the default when Git for Windows is installed).

Paste this one line into Claude Code (desktop app or terminal):

```
Install the Session Guard plugin: run "claude plugin marketplace add ocpipun/session-guard" and "claude plugin install session-guard@session-guard", then tell me to fully quit and reopen Claude.
```

Claude adds the marketplace and installs the plugin. Then fully quit Claude (in the desktop app also from the system tray) and reopen it, **once**. Until then, sessions that were already open run without Session Guard; their messages are blocked with a note to restart.

Installed at user scope, it works in **every project on that machine**. Updates come from here.

## One-time setup (required)

Session Guard does **nothing** until its setup is approved. The setup starts by itself: with your first message after the restart, Claude runs it and asks one question (you can also type `/session-guard:setup`).

1. It scans your global `~/.claude` (CLAUDE.md, memory files, skills, commands, agents, scripts, output styles, rules, hooks) and every project Claude Code has sessions for.
2. It lists everything that conflicts with Session Guard: rules on context limits, hand-offs, breaks, ending sessions, STATUS files or reply style that say something different or repeat Session Guard, and skills, scripts or hooks that do the same job. Project facts and a project's own STATUS layout are not conflicts.
3. It also lists files that do the job of the project files under another name (AGENTS.md, GEMINI.md, .cursorrules, .windsurfrules, .clinerules, copilot-instructions.md, HANDOFF*, NOTES*, TODO*, PROGRESS*, CONTEXT.md, MEMORY.md, status files): conflicting lines are deleted, useful content moves into STATUS.md / STATUS_ARCHIVE.md, missing project files are created.
4. It explains the status line it writes into `~/.claude/settings.json` (terminal quota readings, zero tokens; if you already have another status line, it is left alone and the terminal gives no quota readings; the code is public here, MIT).
5. You approve the list with one answer (you may say an item is not a conflict). The approved items are deleted for good (no backup copies, so nothing half-moved is left behind) and the general rules block is written into `~/.claude/CLAUDE.md` (created if missing; your other rules stay). Session Guard is on at once in every open session; no second restart.
6. **If you do not approve, Session Guard stays off.** Run `/session-guard:setup` again any time.

A project opened later that already has its own CLAUDE.md, similar files, skills or hooks gets the same review once, for that project only, again starting by itself; declining turns Session Guard off in that project only. A new project with nothing to review needs no review. When an update changes the rules, the setup runs again.

## Cloud sessions (claude.ai/code)

Cloud sessions do not install plugins. Session Guard is installed into each repository once, from a cloud session of that repository, with this one line:

```
Install Session Guard: git clone --depth 1 https://github.com/ocpipun/session-guard /tmp/session-guard, then follow /tmp/session-guard/CLOUD.md
```

The line is your approval: in one go Claude writes a hand-off note into `docs/STATUS.md`, copies Session Guard into the repository's `.claude/` folder and commits both straight to the repository's main branch. Then **open a new session** of that repository: there the project review starts by itself (conflicting rules, similar files, project files), and Session Guard stays off until you approve. The installing session and sessions already open stay unprotected. **Once per repository is enough:** every later cloud session runs Session Guard from the start. There is **no quota protection** in the cloud (see above). The cloud has no global CLAUDE.md, so the repository copy brings the general rules itself. On a computer where the plugin is installed, the repository copy stays quiet, so the guard never runs twice.

## Uninstall

Ask Claude to uninstall Session Guard. It first removes its status line (`session_guard.py --uninstall-statusline`), then runs:

```bash
claude plugin uninstall session-guard@session-guard
```

Then remove the block between `<!-- session-guard:begin` and `<!-- session-guard:end -->` from `~/.claude/CLAUDE.md`. For a repository set up for the cloud, ask Claude to remove `.claude/session-guard/`, `.claude/skills/session-guard/`, `.claude/skills/session-guard-setup/` and the Session Guard hooks from `.claude/settings.json`.

## Limits and honest notes

- The context number is the size of the *last reply*, so it can lag one step behind. Thresholds are checked again after every tool call while Claude works.
- A background job or subagent whose "finished" notice never arrives counts as running for at most 24 hours. After that, and when Claude has worked on one message for over 24 hours, Session Guard tells you it may be a bug and suggests `finish session` if it is stuck. A 24-hour timer starts with every prompt (a background hook; the newest prompt replaces the older timer within a minute; the timer is a local check, not a Claude request, so it uses no quota). If 24 hours pass with no new prompt and the session is still busy, it wakes Claude, which tells you. A finished reply waiting for you is not counted. In the cloud the timer dies if the cloud stops an inactive session first.
- Cloud use is based on the official documentation (repository hooks and skills run in single-repository cloud sessions; Python 3 is preinstalled). The v1.2 cloud install (one turn, review in the next session) has not yet been tested in a live cloud session.
- "Every project" means every project Claude Code has a session for (found from `~/.claude/projects`). Folders never opened in Claude Code are not scanned; they are reviewed when first opened.
- Whether an item conflicts is decided by Claude and approved by you; the script only finds the candidates.
- Thresholds are fixed in the script (`NORMAL_STEPS`, `LONG_STEPS`). They are working values, not proven quota savings.

## Tests

```bash
python -m unittest discover -s tests
```

## License

MIT
