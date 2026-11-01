# Session Guard

**Version 1.4** · Open source, released under the **MIT license** · [Türkçe](README.tr.md)

Session Guard is a plugin for Claude Code. It measures three things Claude cannot see by itself: how big the session has grown, how long you were away, and how much of your quota is left. Based on these measurements it tells Claude when to hand work off, when to stop, and it locks finished sessions so nothing typed later can mix things up.

---

## 1. Why it was written

Every message in a Claude Code session re-sends the whole conversation so far (the *context*). The longer the session, the more each message costs. Past a few hundred thousand tokens, one message can cost as much as a whole short session.

Four problems make this worse:

1. **Claude cannot see its own context size.** The app shows the number to you, but the model never receives it. A rule like "hand off at 300k" in a `CLAUDE.md` file is only a guess for Claude.
2. **Long breaks are expensive.** The prompt cache expires after about an hour. The first message after a long break processes the whole context again at full price.
3. **The quota runs out in the middle of a step.** When the 5-hour or weekly limit is reached, work stops wherever it is.
4. **Old sessions get mixed up with new ones.** After a hand-off note is written and work moves to a new session, typing into the old session again creates two different versions of the same work.

Session Guard solves these with measurements, not guesses.

## 2. What it does

### Context thresholds

| Situation | Normal mode | Long mode |
|---|---|---|
| 250k tokens | Notice | Notice |
| 300k | **Hand-off:** STATUS.md is simplified (old tasks and needless detail move to the archive), a hand-off note is written. From then on: no new tasks, only questions | Notice only |
| 350k | Target stop: open a new session | — |
| 400k | **Hard stop**, the session is locked | — |
| 700k | — | Notice; work is made ready to hand off (Claude may keep working) |
| 800k | — | No new work; only simple questions; final hand-off note |
| 900k | — | **Hard stop**, the session is locked |

- Claude acts on a threshold **only when Session Guard says so**. No early warnings, no early hand-off, no "open a new session" because the next task looks big. Session Guard checks after every tool call and stops at 400k, so the plan always holds.
- Every threshold reaches you in one fixed form, for example: "250k threshold passed: notice only. *This is a Session Guard notice."

### Breaks

| Situation | Normal mode | Long mode |
|---|---|---|
| Away over 1 hour, context above 100k | Your message is **not** run. Claude writes the hand-off note, copies your message into it word for word, and the session locks | Break rule off |
| Away over 1 hour, context 100k or less | One-line reminder, then your message runs | Break rule off |

Only *your* absence counts. If Claude is waiting for its own background job (a render, a long script, a subagent), that time is not a break.

### Quota protection (Session Follow, on a computer)

- Session Guard follows the **5-hour** and **weekly (all models)** quota.
- **Desktop app:** Claude reads the app's usage tool when the last reading is older than 20 minutes (from 85% 5-hour or 95% weekly, with every message; during long work at most every 5 minutes). About 400 tokens per reading (an estimate).
- **Terminal:** the numbers come from Session Guard's status line, at no token cost (only if you have no other status line).
- **At 90% (5-hour) or 98% (weekly)**, when the reset is at least 45 minutes away: Claude starts no new task, makes the current work ready to hand off within 15 minutes and writes one line into STATUS.md. Then the session locks until the reset. Sessions opened in the meantime are locked at once. If the reset is closer than 45 minutes, work goes on.
- After the reset the lock lifts by itself.

### Locked sessions

- A locked session never passes a message to Claude. The message is stopped before it is sent, so it costs nothing and changes nothing.
- Lock types are kept apart: context limit, finish session, break, quota (lifts at the reset), restart (a session that started before the install, on a computer).
- When the desktop app reopens an old conversation under a new id, Session Guard recognises it, so the lock (and long mode) stay.

### Other things it does

- **At session start** Claude gets the rules and your `docs/STATUS.md` by itself. You never type "read STATUS.md" again.
- **Project files:** every project gets `CLAUDE.md` (permanent facts and decisions), `docs/STATUS.md` (current state, at most 50 lines) and `docs/STATUS_ARCHIVE.md` (history). STATUS.md is read at every session start, a new session after a hand-off included; the archive is not: Claude searches it only when needed and reads just the matching part, which saves tokens. Existing files are never overwritten.
- **STATUS stays short (backbone rule):** at most 50 lines. At every hand-off and update, old tasks and needless detail move to the archive. If STATUS would pass 50 lines, Claude takes no new task and asks you to finish the pending ones first. Session Guard checks the length on every message.
- **Files doing the same job under another name** (AGENTS.md, GEMINI.md, .cursorrules, HANDOFF*, NOTES*, TODO*, PROGRESS*, status files and similar) are reviewed with your approval: conflicting lines are removed, useful content moves into STATUS or the archive.
- **One rule set:** rules, skills, scripts and hooks that conflict with Session Guard are removed during setup, with your approval.
- **Reply style that saves tokens:** plain words, short sentences, each issue numbered, only the labels needed: PROBLEM / RECOMMENDATION / ASSUMPTION / WARNING / UNVERIFIED (in your language).
- **Facts stay facts:** what Claude has not checked is labelled UNVERIFIED; its own recommendations and assumptions are labelled as its own.
- **Notices in your language:** the notices the app shows directly (locks, blocked messages, thresholds) come in the language chosen at setup. Details in section 5.

## 3. Where it can be used

| Place | Works | Notes |
|---|---|---|
| Claude desktop app (computer) | Yes | Full feature set, including quota protection. Tested on Windows. |
| Claude Code in a terminal (computer) | Yes | Quota from the status line. Notices in English. |
| Phone or browser connected to a computer session (Remote Control) | Yes | The session runs on the computer, so the computer rules apply. |
| Cloud sessions (claude.ai/code, also from phone or browser) | Yes, per repository | No quota protection. Installed into the repository. |
| macOS and Linux desktop app | Expected | Built for them, **not tested**. |

**One home per project (strict rule).** A project is worked on **either on the computer or in the cloud, never both**, and never at the same time. Quota protection works only on the computer, and two places writing one STATUS.md drift apart.
- A computer project that is a git repository gets a one-line hook in its `.claude/settings.json`. On the computer it does nothing; in a cloud session of that repository it stops every message before it reaches Claude.
- A cloud project carries Session Guard's cloud copy in `.claude/session-guard/`. Opened on a computer, every message is stopped.
- **The computer guard goes to GitHub at once.** It blocks cloud sessions only once it is on GitHub, so setup commits and pushes it right away in each git project (with Session Guard's files and the files setup changed there; your other changes are left alone). A new project's guard is pushed at its first session.
- A repository used both on the computer and in the cloud before becomes a computer project during setup (its cloud copy is removed, the guard pushed). Cloud sessions opened before cannot be locked from the computer: archive them at claude.ai/code.
- On a computer no GitHub repository is needed: Session Guard lives in `~/.claude` and puts only the three project files into a project. The cloud always needs a repository.

## 4. Install

### On a computer

Requirements: Claude Code (desktop app or terminal) and Python 3 (`python` on Windows, `python3` on macOS/Linux). On Windows, Claude Code should use Git Bash for hooks (the default when Git for Windows is installed).

**Strict rule: install and updates run only in a Claude project folder named `session-guard-executive`.** Without this folder Session Guard does not work in computer sessions.

1. Create a folder named `session-guard-executive` anywhere and open it in Claude.
2. In a session there, paste this one line:

   ```
   Install Session Guard: run "claude plugin marketplace add ocpipun/session-guard" (if it is already added: "claude plugin marketplace update session-guard"; if "claude" is not found, use the program in $CLAUDE_CODE_EXECPATH), then follow INSTALL.md in ~/.claude/plugins/marketplaces/session-guard
   ```

3. Claude downloads Session Guard and scans your global `~/.claude` folder and every project Claude Code knows. It shows **one list**: conflicting rules, skills, scripts and hooks, files doing the project files' job under another name, and leftovers of an older Session Guard. The same message says what else setup does: the general rules block in `~/.claude/CLAUDE.md`, the missing project files, the status line in `~/.claude/settings.json` and the computer guard in git projects, which is pushed to GitHub at once. If the app language cannot be read, it also asks: "Which language would you like session-guard notices in?"
4. You answer **once**. Approved items are deleted for good (no backup copies). **If you decline, nothing is installed.**
5. Claude installs the plugin (an older version is simply updated; nothing has to be removed by hand).
6. Last step: **fully quit Claude** (in the desktop app also from the system tray) **and reopen it.** Until then, open sessions run as before. After reopening you type nothing and are asked nothing; you can go on in the same conversations.

It then works in every project on that computer. A project opened later that has its own CLAUDE.md, similar files, skills or hooks gets the same review once, for that project only; it starts by itself.

### In the cloud

Cloud sessions do not install plugins. Session Guard is installed into each repository once, from a cloud session of that repository:

```
Install Session Guard: git clone --depth 1 https://github.com/ocpipun/session-guard /tmp/session-guard, then follow /tmp/session-guard/CLOUD.md
```

Claude shows the repository's review list, asks "Which language would you like session-guard notices in?" and one approval question. If you decline, nothing is installed. If you approve, Claude installs Session Guard into `.claude/`, creates the missing project files and commits everything straight to the repository's main branch. **You go on in the same session:** Session Guard runs there from your next message, and in every new session of that repository. Sessions that were already open stay unprotected. A computer project is refused (one home per project).

## 5. Commands and everyday use

### Commands you type

Type them at the **end** of a message, or at the **start** followed by a comma or period. Anywhere else (inside a sentence, a quote, pasted text) they are ignored on purpose.

| Command | What it does |
|---|---|
| `start long mode` | Long mode for this session only. It cannot be switched off; a new session starts in normal mode. For long, complex or unattended work. |
| `finish session` | Ends the topic: hand-off note and usage measurement, then the session locks. Use it whenever a topic is done, so one session never mixes several topics. |

Examples: `Start long mode, render the remaining clips` · `That's all for today, finish session`

### Things you ask Claude

| You want to | Do this |
|---|---|
| Update Session Guard (computer) | In `session-guard-executive`, paste the install line again |
| Update Session Guard (cloud) | Paste the cloud install line again in a cloud session of that repository |
| Run the setup or review again | Type `/session-guard:setup` |
| Notices in another language | Ask Claude, for example "Session Guard notices in French". Claude translates them; until then they are English |
| Uninstall | Ask Claude to uninstall Session Guard (details below) |

### Notice language

- On a computer the language selected in the Claude desktop app's settings is read **once, at setup**, and never checked again. Turkish comes with the plugin; other languages are translated by Claude during setup.
- In the cloud the app language cannot be read, so Claude asks during setup.
- In the terminal notices are English.
- Three notices are always English, because they can appear before or outside setup: the missing `session-guard-executive` folder, a cloud project opened on a computer, and a computer project opened in the cloud.

### Uninstall

Ask Claude to uninstall Session Guard. It removes its status line (`session_guard.py --uninstall-statusline`) and runs:

```bash
claude plugin uninstall session-guard@session-guard
```

Then remove the block between `<!-- session-guard:begin` and `<!-- session-guard:end -->` from `~/.claude/CLAUDE.md`. In a repository set up for the cloud, ask Claude to remove `.claude/session-guard/`, `.claude/skills/session-guard/`, `.claude/skills/session-guard-setup/` and the Session Guard hooks from `.claude/settings.json`.

## 6. How it works

- **Hooks** run Session Guard's script by themselves: at session start, on every message you send and after every tool Claude uses. A fourth hook in the background is the 24-hour watch.
- **The script** (`scripts/session_guard.py`, Python standard library only) reads the session record Claude Code already keeps on your disk. The context size comes from the token usage of Claude's last reply; the break is the time since that reply. At a threshold it adds an instruction with the measured numbers; a normal message gets nothing, so it costs no tokens.
- **Skills** hold the procedures (setup and review, hand-off, break hand-off, finish session). Claude loads them only when needed.
- **State** is Session Guard's own small record in `~/.claude/session_guard/`: long mode, locks and notices per session; the shared quota readings; the setup record, notice language and your own translations. No project file or conversation is ever kept there.

**Nothing is sent anywhere.** The script only reads local files and writes its own files.

## 7. Limitations

- The context number is the size of the *last reply*, so it can be one step behind. Thresholds are checked again after every tool call.
- **No quota protection in the cloud:** no hook can read the quota there (checked against the documentation and tested).
- In the desktop app each quota reading costs tokens (about 400, an estimate); the terminal status line costs none.
- The app language comes from the desktop app's own settings file (`config.json`, key `locale`). This file is not documented. It was measured on Windows; on macOS (`~/Library/Application Support/Claude/`) and Linux (`~/.config/Claude/`) it comes from the app's code and is **not tested**. If it is not found, Claude asks.
- On a computer, sessions that started before the install are protected only after the app is restarted.
- A project lives on the computer or in the cloud, not both (see section 3).
- A background job whose "finished" notice never arrives counts as running for at most 24 hours. After that, or when Claude works on one message for over 24 hours, Session Guard says it may be a bug. The 24-hour timer is a local check and uses no quota. In the cloud it stops if the cloud ends an idle session first.
- "Every project" means every project Claude Code has a session for. Folders never opened in Claude Code are reviewed when first opened.
- Whether something conflicts is decided by Claude and approved by you; the script only finds the candidates.
- The thresholds are fixed in the script (`NORMAL_STEPS`, `LONG_STEPS`, `STOP_AT`). They are working values, not proven quota savings.
- The desktop install of v1.4 has not yet been tested in a live install; the cloud install and update were tested (v1.2).

## 8. Tests

```bash
python -m unittest discover -s tests
```

## 9. License: open source, MIT

Session Guard is **open source** and released under the **MIT license** (see [LICENSE](LICENSE)). You may use, copy, change and share it freely, also commercially. It comes **as is, without any warranty**. If something does not fit your setup (another language, another operating system, other thresholds), you are free to change it yourself.
