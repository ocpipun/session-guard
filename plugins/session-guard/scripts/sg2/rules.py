# SESSION-GUARD: 2.0 | component: sg2-rules | version: 2.0.0
"""The rules Claude gets at session start, and the general rules block of ~/.claude/CLAUDE.md.

Threshold, break, long-mode, quota, archive and reply-style rules are v1.4's, unchanged. New in 2.0: ids, task
ownership, the generated STATUS block, per-task hand-offs and migration.
"""
import os

from . import core

RULES_VERSION = 5   # 4 = v1.4
BLOCK_BEGIN = ("<!-- session-guard2:begin (SESSION-GUARD: 2.0; managed by the Session Guard plugin; changes inside "
               "this block are overwritten) -->")
BLOCK_END = "<!-- session-guard2:end -->"

RULES = """[Session Guard 2.0] Rules for this session (plugin session-guard, SESSION-GUARD: 2.0).
- You cannot measure context size or time between messages. Session Guard measures them itself and writes to
  you ("[Session Guard] Context: ...") only when it has an instruction. Follow it. Quote its numbers; never
  invent one. Never add context size or mode to your replies on your own.
- Each threshold instruction gives the user one line: "<N>k threshold passed: <what now>. *This is a Session
  Guard notice." (in the user's language); during work give it and keep working.
- Act on a threshold only when Session Guard's instruction for it arrives, never earlier on your own: no early
  notice, no hand-off, no "open a new session" for size or cost. Session Guard checks after every tool call and
  stops at 400k (900k long mode). 250k (and 300k in long mode) is a notice only. Strict rule.
- Normal mode: 250k notice; 300k hand-off, then no new tasks (only information questions and hand-off updates
  on request); 350k target stop; 400k hard stop. Long mode ("start long mode"; this session only, cannot be
  switched off): 250k/300k notice only; 700k notice, keep task notes current, no hand-off yet; 800k hand-off,
  then no work, only simple specific questions; 900k hard stop. The break rule does not apply in long mode.
  After the 300k hand-off (normal) or the 800k hand-off (long) Session Guard refuses "start long mode" and
  "finish session" itself; the hand-off is not written a second time, and the session measurement of
  "finish session" is done with that hand-off.
- Break (normal mode): gap > 1 hour and context > 100k -> do NOT run the user's command and do no reading or
  research for it; hand off every task you own (Session Guard already stored the command word for word).
  Context <= 100k -> remind in one line, then continue. Waiting for your own background job is not a break.
- "finish session" (any mode): only hand-offs and session measurement, nothing else. Commands ("start long mode",
  "finish session", "continue installation") count only at the end of the message, or at its start followed by
  a comma or period. After a hard stop, a break hand-off, "finish session", a quota stop, a completed hand-over
  or for a pre-2.0 conversation Session Guard locks the session: prompts never reach you.
- IDS: every project has a PROJECT-ID, every session a SESSION-ID (yours is given below and in $SG_SESSION_ID),
  every task a TASK-ID (TASK-001, subtasks TASK-001-1, TASK-001-2-1). Use `sg` (the command given below) for every
  task change; it checks ownership and writes under a lock, so parallel sessions never overwrite each other.
- TASKS: work only on a task you own. Continuing a task keeps its id (claim it: `sg task claim <id>`). A task owned
  by another active session is not yours: ask the user. A new active main task needs the user's approval
  (`--approval "<their words>"`); at most 3 normal main tasks, plus 1 urgent with a reason and approval when 3 are
  full. Subtasks are free. Unrelated work goes to the backlog (`sg idea add`), not into a new task. Redoing a task
  finished wrongly, or adding a feature to a finished one, is a NEW task (`sg task reopen <id>`), linked to the
  old one. A finished subtask never finishes its parent.
- STATUS: the task block of docs/STATUS.md is generated from the task store: never edit it by hand. When a step
  ends run `sg task note`, when a task is finished `sg task done` at once, not at session end. Free text goes
  under "## Notes" (STATUS.md stays at 50 lines or less; move old notes to STATUS_ARCHIVE.md). Work by section
  (its heading or TASK-ID), never by line number: delete a line completely, leave no empty line or empty bullet
  (Session Guard removes them). A rule lives only in CLAUDE.md, a task or state only in the task store / STATUS:
  never the same line in both.
- RULES TO SCRIPTS: when CLAUDE.md gets new rules Session Guard asks for a RULE REVIEW: propose a script or hook
  for every rule one could enforce (as a backlog idea the user decides on), so checks move from you to scripts.
- SESSION START: if several tasks can be taken over (hand-offs waiting, orphaned tasks), do not pick one: ask the
  user which. Read a hand-off with `sg task show <id>` before claiming it.
- HAND-OFF: first bring each task you own to a hand-off-ready point (finish the small step in progress, leave
  nothing half-written), then per task `sg handoff write <id> --technical "..."`, then `sg handoff publish` (skill
  session-guard). The next session must find the work and know where it is:
  - Cloud (always): `handoff write` commits the task's unfinished work and `handoff publish` pushes it to the
    task's own side branch sg/<task-id> (verified); a hand-off is not done before that. Several tasks: name each
    task's files with --paths.
  - Computer: the work stays in the folder (the record lists the uncommitted files); no side branch, no push.
    Use a side branch (--side-branch) only when the user asks for it, or you propose it and the user approves.
  A task already handed off and unchanged, or finished, is not handed off again.
- TAKING OVER: a hand-off on a side branch this checkout does not have is listed at session start ("HAND-OFF
  BRANCHES"); `sg handoff adopt <branch>` merges its work and record here, then claim the task.
- VERY IMPORTANT: docs/STATUS_ARCHIVE.md is NOT read at session start, also not after a hand-off. Read it only
  when needed, and then only the matching part, found by search (Grep); never the whole file.
- Quota (computer only): when Session Guard asks for a quota reading, call the usage tool it names silently, in
  parallel with your next tool call; Session Guard reads the result itself.
- Procedures: skill session-guard. Install, migration: skill setup.
- Reply style (saves tokens): plain words, short sentences, no needless detail. Give each issue its own number.
  Under it, only those needed, the labels PROBLEM / RECOMMENDATION / ASSUMPTION / WARNING / UNVERIFIED, in the
  user's language. Never present as fact what you have not checked: label it UNVERIFIED.
- Always answer in the user's language."""

GENERAL_RULES = """# Session Guard 2.0 general rules (SESSION-GUARD: 2.0)
- These rules and the "[Session Guard 2.0] Rules" given at session start win over any other CLAUDE.md rule on
  the same topic, project files included. On other topics a project CLAUDE.md wins over the global one.

## Project files
- `CLAUDE.md` permanent rules and decisions; `docs/STATUS.md` current state (task block generated by Session
  Guard, at most 50 lines); `docs/STATUS_ARCHIVE.md` history (append only, searched, never read whole);
  `docs/TASK_BACKLOG.md` waiting ideas; `docs/COMPLETED_TASKS.md` one line per finished task.
- Tasks change only through `sg` commands (ownership, lock, ids). No separate hand-off files, no second archive.
- Update the task when a step ends, before long work, on an error, while waiting for the user, before a break.

## Topics
- Suggest a new conversation when the topic changes, whatever the token count. Unrelated work goes to the backlog.

## Tools and permissions
- Ask for screen, app and similar permissions right before the action that needs them, not at session start.
- Load deferred tools one by one as needed (ToolSearch with `select:`), never in bulk.

## Session measurement
- On "finish session" read the weekly and 5-hour quota percentages if the app has a usage tool, run the
  `report` command Session Guard gives and add one row to the "Session measurements" table of
  STATUS_ARCHIVE.md. A value you cannot read is "n/a"; never invent a number. Never present a token drop as a
  quota saving."""


def project_rule_lines(root):
    """The rule lines of a project's CLAUDE.md (no headings, comments, code fences or Session Guard blocks)."""
    p = core.exists_ci(root, "CLAUDE.md") if root else None
    lines, inside, fence = [], False, False
    for line in (core.read_text(p) or "").splitlines() if p else []:
        s = line.strip()
        if s.startswith("```"):
            fence = not fence
            continue
        if "session-guard" in s and ":begin" in s:
            inside = True
        if inside:
            inside = not ("session-guard" in s and ":end" in s)
            continue
        if fence or not s or s.startswith(("#", "<!--")) or s in lines:
            continue
        lines.append(s)
    return lines


def new_rule_lines(root, data):
    seen = set(((data or {}).get("rule_review") or {}).get("lines") or [])
    return [l for l in project_rule_lines(root) if l not in seen]


RULE_REVIEW = ("[Session Guard 2.0] RULE REVIEW: CLAUDE.md has %d new or changed rule line(s):\n%s\nFor each: could a "
               "script or hook check or do it instead of you (file checks, formats, limits, names, commands, git "
               "steps)? Look at what the project already has. Propose each fitting one as a task proposal, not work: "
               "`%s idea add \"Script instead of a rule: <rule> -> <what the script does>\" --source rule-review`, "
               "and list them for the user in your reply, in one numbered list. A line that is state or a task, not a "
               "rule, belongs in a task or STATUS: say so and, with the user's yes, move it. Then `%s rules reviewed`. "
               "Do this once now, briefly, before or alongside the user's request.")


def global_md():
    return os.path.join(core.claude_dir(), "CLAUDE.md")


def ensure_global_block():
    """Writes the 2.0 block (the user's own text is kept). 'same', 'updated', 'added' or 'broken'."""
    p = global_md()
    try:
        with open(p, encoding="utf-8-sig", newline="") as fh:
            text = fh.read()
    except FileNotFoundError:
        text = ""
    nl = "\r\n" if "\r\n" in text else "\n"
    block = (BLOCK_BEGIN + "\n" + GENERAL_RULES + "\n" + BLOCK_END).replace("\n", nl)
    opener = BLOCK_BEGIN.split(" (")[0]
    starts, ends = text.count(opener), text.count(BLOCK_END)
    if starts == ends == 0:
        new, result = (block + nl + nl + text if text.strip() else block + nl), "added"
    elif starts == ends == 1 and text.index(opener) < text.index(BLOCK_END):
        a, b = text.index(opener), text.index(BLOCK_END) + len(BLOCK_END)
        if text[a:b] == block:
            return "same"
        new, result = text[:a] + block + text[b:], "updated"
    else:
        return "broken"
    core.write_text(p, new)
    return result


def remove_v1_block(force=False):
    """Removes the 1.x block, only once the 2.0 block is in place (force: on uninstall). 'removed', 'none', 'kept'
    or 'broken'."""
    from . import legacy
    p = global_md()
    text = core.read_text(p) or ""
    if legacy.V1_BLOCK not in text:
        return "none"
    if BLOCK_BEGIN.split(" (")[0] not in text and not force:
        return "kept"  # never leave the computer without any rules block
    a, b = text.find(legacy.V1_BLOCK), text.find(legacy.V1_BLOCK_END)
    if text.count(legacy.V1_BLOCK) != 1 or b < a:
        return "broken"
    b += len(legacy.V1_BLOCK_END)
    while b < len(text) and text[b] in "\r\n":
        b += 1
    core.write_text(p, text[:a] + text[b:])
    return "removed"


def remove_global_block():
    p = global_md()
    text = core.read_text(p) or ""
    opener = BLOCK_BEGIN.split(" (")[0]
    a, b = text.find(opener), text.find(BLOCK_END)
    if a < 0 or b < a:
        return "none"
    b += len(BLOCK_END)
    while b < len(text) and text[b] in "\r\n":
        b += 1
    core.write_text(p, text[:a] + text[b:])
    return "removed"
