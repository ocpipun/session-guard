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
| Break over 1 hour, context > 125k | Your prompt is **not** run. Claude writes the hand-off note, copies your prompt into it word for word, and the session locks | Break rule off |
| Break over 1 hour, context ≤ 125k | One-line reminder, then your prompt runs | Break rule off |
| `finish session` | Hand-off + usage measurement, then the session locks | same |

- **The break rule only counts *your* absence.** If Claude started a background job (a render, a long script, a subagent) and is waiting for it, the gap is not a break.
- **A locked session never passes a prompt to Claude.** The prompt is blocked before it is sent, so it costs nothing and changes nothing. You see "Session closed, open a new session".
- **Reopened conversations stay locked.** The desktop app can reopen an old conversation under a new session id. Session Guard recognises it by its message ids, so the lock (and long mode) survive.
- **Session start:** Claude gets the rules and your `docs/STATUS.md` automatically. You never type "read STATUS.md" again; the archive (`docs/STATUS_ARCHIVE.md`) is only searched when needed.
- **Reply style that saves tokens:** plain words, short sentences, each issue numbered, with only the labels it needs: PROBLEM / RECOMMENDATION / ASSUMPTION / WARNING / UNVERIFIED (in your language). Claude never presents a guess as fact. If you want more detail, ask.
- **Any language.** Commands and the plugin's own texts are English; Claude talks to you in your language.

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
  - adds a status line and, at a threshold, an instruction to Claude's context,
  - shows you a short message when a threshold is first passed,
  - blocks prompts in a locked session,
  - stops Claude's turn if a hard limit is reached in the middle of work.
- **Skills** hold the procedures (hand-off, break hand-off, finish session, cloud setup). Claude loads them only when needed.
- **State** (long mode, lock, notified thresholds) is a small JSON file per session in `~/.claude/session_guard/`. Files older than 30 days are deleted.

Nothing is sent anywhere. The script only reads local files and writes its own state files.

## Install

Requirements: Claude Code, Python 3 (`python` on Windows, `python3` on macOS/Linux). On Windows, Claude Code should use Git Bash for hooks (the default when Git for Windows is installed).

In a terminal:

```bash
claude plugin marketplace add ocpipun/session-guard
claude plugin install session-guard@session-guard
```

Or inside Claude Code: `/plugin marketplace add ocpipun/session-guard`, then `/plugin install session-guard@session-guard`.

Installed at user scope, it works in **every project on that machine**. Start a new session after installing. Nobody needs to copy this repository; updates come from here.

## Cloud sessions (claude.ai/code)

Cloud sessions do not install plugins. They do run the hooks and skills that live **inside the repository**. So for each repository you use in the cloud, run once on your machine:

```
/session-guard:cloud-setup
```

It copies the script, the hooks and the hand-off skill into the repository's `.claude/` folder. Commit and push; from then on cloud sessions of that repository are guarded too. On a machine where the plugin is installed, that project copy stays quiet, so the guard never runs twice.

## Uninstall

```bash
claude plugin uninstall session-guard@session-guard
```

Or just ask Claude to uninstall it. For a repository set up for the cloud, ask Claude to remove `.claude/session-guard/`, `.claude/skills/session-guard/` and the Session Guard hooks from `.claude/settings.json`.

## Limits and honest notes

- The context number is the size of the *last reply*, so it can lag one step behind. Thresholds are checked again after every tool call while Claude works.
- A background job whose "finished" notice never arrives counts as running for at most 24 hours.
- Cloud use is based on the official documentation (repository hooks and skills run in single-repository cloud sessions; Python 3 is preinstalled). It has not yet been tested in a live cloud session.
- Thresholds are fixed in the script (`NORMAL_STEPS`, `LONG_STEPS`). They are working values, not proven quota savings.

## Tests

```bash
python -m unittest discover -s tests
```

## License

MIT
