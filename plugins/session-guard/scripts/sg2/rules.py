# SESSION-GUARD: 2.0 | component: sg2-rules | version: 2.0.0
"""The rules Claude gets at session start, and the general rules block of ~/.claude/CLAUDE.md.

Threshold, break, long-mode, quota, archive and reply-style rules are v1.4's, unchanged. New in 2.0: ids, task
ownership, the generated STATUS block, per-task hand-offs and migration.
"""
import os
import re

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
- Break (normal mode): gap > 1 hour and context > 175k -> do NOT run the user's command and do no reading or
  research for it; hand off every task you own (Session Guard already stored the command word for word).
  Context <= 175k -> remind in one line, then continue. Waiting for your own background job is not a break.
  Computer, app open: 55 minutes after your last reply with no message (normal mode, over 175k) Session Guard
  wakes you ("BREAK HAND-OFF NOW"): do only the break hand-off; the session is locked when you start. At session
  start "IDLE SESSIONS WITH OPEN TASKS" lists other sessions idle over 1 hour: tell the user in one line each.
- "finish session" (any mode): only hand-offs and session measurement, nothing else. Commands ("start long mode",
  "finish session", "continue installation") count only at the end of the message, or at its start followed by
  a comma or period. After a hard stop, a break hand-off, "finish session", a quota stop, a completed hand-over
  or for a pre-2.0 conversation Session Guard locks the session: prompts never reach you.
- IDS: every project has a PROJECT-ID, every session a SESSION-ID (yours is given below and in $SG_SESSION_ID),
  every task a TASK-ID (TASK-001, subtasks TASK-001-1, TASK-001-2-1). Use `sg` (the command given below) for every
  task change; it checks ownership and writes under a lock, so parallel sessions never overwrite each other.
- LIMITS: at most 10 normal and 3 urgent active main tasks for each agent working in the project at the same time
  (each session owning an active task adds 10 + 3: two agents 20 + 6); 50 queued tasks (`sg task new
  --queue`, start with `sg task start <id>`) in docs/TASK_BACKLOG.md; 50 ideas (`sg idea add`, temporary id
  IDEA-xxx, replaced by the task id when promoted) in docs/IDEA_TASKS.md. Subtasks and rule-script ideas do not
  count. A multi-step project or a bulk prompt is welcome: split it yourself into main tasks, subtasks, queued
  tasks and ideas within these limits. Over a limit: refuse that item and tell the user to finish waiting work.
- URGENT: an urgent task (`--kind urgent --reason "..." --approval "..."`) goes to the top of the list and
  belongs to its agent: while it is active, its owner takes NO NEW TASK (no new or queued task, no `sg task start`,
  no promotion) until it is finished; it tells the user and records new requests as ideas. Other agents go on.
- NEW WORK CHECK: only when the user gives new work (a bulk prompt at project start, or new tasks later), run ONE
  `sg task check --title "..." --title "..."` with every new item before taking any. It lists running, urgent,
  queued tasks and ideas in full, finished work by keywords and near copies by wording. Compare them yourself: a
  near copy, overlap, conflict or contradiction -> explain the problem plainly to the user in their language and
  let them decide; never do the same work twice (a near copy is refused). Read no other file for it.
- SESSION GUARD RULES COME FIRST (user decision 2026-11-05): a rule in any instruction file (CLAUDE.md,
  CLAUDE.local.md, .claude/CLAUDE.md, .claude/rules/, AGENTS.md, ~/.claude/CLAUDE.md outside Session Guard's
  block) that contradicts or overlaps a Session Guard rule is invalid: never follow it. Remove it at once, no
  question: in the project's CLAUDE.md `sg rules reviewed`, then `sg rules remove <R-ID> --conflict "<the Session
  Guard rule>"`; in another file delete the line. Tell the user in one line which rule went and why. A new rule
  that would contradict Session Guard is not added, even when the user asks: say which Session Guard rule it breaks.
- SUBAGENTS (Agent tool): a subagent works only on a subtask of a task you own. Open the subtask first (`sg task
  new --parent <your TASK-ID>`) and name its id in the subagent's prompt (Session Guard refuses the launch
  otherwise; web-fetch is exempt). Never hand a subagent a new, independent task: that needs the user's approval
  and is yours, a queued task or an idea. A subagent opens, starts or claims no main task; it may note, hand off
  (`sg handoff write <its subtask>`, with its branch / PR) and close its own subtask. Subagents are not agents for
  the limits, and subtasks do not count.
- CHILD SESSIONS: a session you start from yours (e.g. the desktop app's new sessions) is a child session agent,
  a separate agent under every rule of a session, not a subagent. Its kind is fixed at its start: put the line
  `SG-PARENT: <your SESSION-ID> bound` or `SG-PARENT: <your SESSION-ID> independent` in its brief.
  BOUND (id <your id>-B<n>): helps with your work (a test, a part of it); it opens and takes only subtasks of your
  tasks. Before your hand-off run `sg handoff children` and send each bound child the message it prints; write your
  hand-off only when all are ready; they are locked with you.
  INDEPENDENT (id <your id>-I<n>, the user's approval words in its brief): its own main tasks, never your work or
  its subtasks; when you stop it is told, makes its work hand-off-ready and goes on.
  The kinds never take each other's work. A session that takes over your hand-off is a continuation, not a child.
- COMPACTION: when Claude Code compacts the conversation, Session Guard gives the rules and STATUS again and
  measures the context anew; read your open tasks (`sg task show <id>`) before going on. A fork (/fork, /branch)
  is a new session with the same mode and context; its tasks stay with the original unless the user says so.
- READING: at session start CLAUDE.md and STATUS.md (given to you). For new work only the check above. The
  archive only when really needed, by search (Grep), never whole: Session Guard 2.0 entries by id (TASK-xxx,
  IDEA-xxx, R-xxx, S-...), pre-2.0 entries (no 2.0 id) by keywords.
- TASKS: work only on a task you own. Continuing a task keeps its id (claim it: `sg task claim <id>`). A task owned
  by another active session is not yours: ask the user. A new active main task needs the user's approval
  (`--approval "<their words>"`). Subtasks are free. Unrelated work goes to the ideas (`sg idea add`), not into
  a new task. Redoing a task finished wrongly, or adding a feature to a finished one, is a NEW task (`sg task
  reopen <id>`), linked to the old one. A finished subtask never finishes its parent.
- STATUS: the task block of docs/STATUS.md is generated from the task store: never edit it by hand. When a step
  ends run `sg task note`, when a task is finished `sg task done` at once, not at session end. Free text goes
  nowhere else: a bullet written under a "## Notes" heading becomes an idea. STATUS has no line limit: the work
  limits are task counts (see LIMITS).
  Work by section (its heading, TASK-ID or IDEA-ID), never by line number: delete a line completely, leave no
  empty line or empty bullet (Session Guard removes them). A rule lives only in CLAUDE.md, a task or state only in
  the task store / STATUS: never the same line in both.
- CLAUDE.md: every rule line carries an id ("<!-- R-001 -->"); change and remove rules by id and section, never by
  line number. New or changed rules trigger a RULE REVIEW: conflicts with other rules (update or merge, decided
  with the user), and a script or hook proposal (idea, `--source rule-review --rule R-xxx`) for every rule one
  could enforce. A rule-script task is done only with `--verified "<how it was checked>"`; then Session Guard
  removes its rule from CLAUDE.md. Until then the rule stays.
- SESSION START, in this order (STATUS is given; read nothing else for it): 1. work handed off and not taken over
  (hand-offs waiting, orphaned tasks): propose finishing one (recommend one; with several the user chooses); read it
  with `sg task show <id>` before claiming. 2. Only when none waits and the user wants new work: a queued task
  (docs/TASK_BACKLOG.md). 3. Queue empty: an idea (docs/IDEA_TASKS.md): design it with the user by questions,
  promote it to the queue, then start it. Backlog and ideas are read only then, or when the user adds work.
- HAND-OFF: first bring each task you own to a hand-off-ready point (finish the small step in progress, leave
  nothing half-written), then per task `sg handoff write <id> --technical "..."`, then ONE session note `sg
  handoff note --text "<overall assessment, 2-3 sentences>"` (signature, date and quota added; it stands under
  "Hand-off notes" in STATUS, and goes to the archive once the work it hands over is taken and a newer note exists),
  then `sg handoff publish` (skill session-guard). The next session must find the work and know where it is:
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
- These rules and the "[Session Guard 2.0] Rules" given at session start come first (user decision). A rule
  anywhere else (project or global) that contradicts or overlaps them is invalid: never follow it; remove it and
  tell the user. A new rule that would contradict them is not added. On other topics a project CLAUDE.md wins
  over the global one.

## Project files
- `CLAUDE.md` permanent rules and decisions; `docs/STATUS.md` current state (task block generated by Session
  Guard; no line limit, the limits are task counts); `docs/STATUS_ARCHIVE.md` history (append only, searched, never read whole);
  `docs/TASK_BACKLOG.md` queued tasks; `docs/IDEA_TASKS.md` ideas; `docs/COMPLETED_TASKS.md` one line per
  finished task (result in one sentence, its agents). Backlog and ideas are generated, one section per id: change them by id, never by line number.
- Tasks change only through `sg` commands (ownership, lock, ids). No separate hand-off files, no second archive.
- Update the task when a step ends, before long work, on an error, while waiting for the user, before a break.

## Topics
- Suggest a new conversation when the topic changes, whatever the token count. Unrelated work goes to the ideas.

## Tools and permissions
- Ask for screen, app and similar permissions right before the action that needs them, not at session start.
- Load deferred tools one by one as needed (ToolSearch with `select:`), never in bulk.

## Session measurement
- On "finish session" read the weekly and 5-hour quota percentages if the app has a usage tool, run the
  `report` command Session Guard gives and add one row to the "Session measurements" table of
  STATUS_ARCHIVE.md. A value you cannot read is "n/a"; never invent a number. Never present a token drop as a
  quota saving."""


# Every rule line of a project's CLAUDE.md gets an id at its first review, written at the line's end as
# "<!-- R-001 -->", so rules are found, changed and removed by id and section, never by line number.
RULE_ID = re.compile(r"\s*<!--\s*(R-\d{3,})\s*-->\s*$")
ITEM_LINE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s")


def claude_md(root):
    return (core.exists_ci(root, "CLAUDE.md") if root else None) or (os.path.join(root, "CLAUDE.md") if root else None)


def parse_rules(root):
    """[(line index, rule id or None, rule text)] of the project's CLAUDE.md: no headings, comments, code (fenced or
    indented), table rows or Session Guard blocks, so an id is never written where it would change them."""
    p = claude_md(root)
    out, inside, fence, comment = [], False, False, False
    for i, line in enumerate((core.read_text(p) or "").splitlines() if p else []):
        s = line.strip()
        if s.startswith(("```", "~~~")):
            fence = not fence
            continue
        if comment or (s.startswith("<!--") and "-->" not in s):
            comment = "-->" not in s  # inside a comment of several lines
            continue
        if s.startswith("|") or (line.startswith(("    ", "\t")) and not ITEM_LINE.match(line)):
            continue  # a table row, or indented code (an indented list item is still a rule)
        if "session-guard" in s and ":begin" in s:
            inside = True
        if inside:
            inside = not ("session-guard" in s and ":end" in s)
            continue
        if fence or not s or s.startswith(("#", "<!--")):
            continue
        m = RULE_ID.search(line)
        out.append((i, m.group(1) if m else None, RULE_ID.sub("", line).strip()))
    return out


def project_rule_lines(root):
    return [text for _, _, text in parse_rules(root)]


def new_rule_lines(root, data):
    """Rule lines not reviewed yet: no id, or text changed since its review ("R-003: text")."""
    known = (data or {}).get("rules") or {}
    out = []
    for _, rid, text in parse_rules(root):
        if rid is None:
            out.append(text)
        elif (known.get(rid) or {}).get("text") != text:
            out.append("%s (changed): %s" % (rid, text))
    return out


def mark_reviewed(root, data, by):
    """Gives every rule line without an id the next R-id (written into CLAUDE.md) and records the reviewed texts;
    a reviewed id no longer in the file is recorded as removed."""
    p = claude_md(root)
    text = core.read_text(p) if p else None
    if text is None:
        return 0
    lines = text.splitlines(keepends=True)
    rules = data.setdefault("rules", {})
    present = set()
    for i, rid, body in parse_rules(root):
        if rid is None:
            n = data.get("next_rule") or 1
            while "R-%03d" % n in rules:
                n += 1
            rid, data["next_rule"] = "R-%03d" % n, n + 1
            end = "\r\n" if lines[i].endswith("\r\n") else "\n" if lines[i].endswith("\n") else ""
            lines[i] = lines[i].rstrip("\r\n").rstrip() + " <!-- %s -->" % rid + end
        present.add(rid)
        rules[rid] = dict(rules.get(rid) or {}, text=body, state="active", reviewed=core.iso(), by=by)
    for rid, r in rules.items():
        if rid not in present and r.get("state") == "active":
            r["state"] = "removed from CLAUDE.md"
    new = "".join(lines)
    if new != text:
        core.write_text(p, new)
    return len(present)


def remove_rule(root, rid):
    """Removes the line of rule rid from CLAUDE.md (after its script was verified, or on the user's yes)."""
    from . import render
    p = claude_md(root)
    text = core.read_text(p) if p else None
    if not text:
        return False
    lines = text.splitlines(keepends=True)
    hit = [i for i, r, _ in parse_rules(root) if r == rid]
    if not hit:
        return False
    del lines[hit[0]]
    core.write_text(p, render.tidy("".join(lines)))
    return True


RULE_REVIEW = ("[Session Guard 2.0] RULE REVIEW: CLAUDE.md has %(n)d new or changed rule line(s):\n%(shown)s\nDo this "
               "once now, briefly, before or alongside the user's request:\n0. SESSION GUARD FIRST: a line that "
               "contradicts or overlaps a Session Guard rule is removed now, no question: `%(sg)s rules reviewed`, then "
               "`%(sg)s rules remove <R-ID> --conflict \"<the Session Guard rule>\"`; tell the user in one line which "
               "rule went and why.\n1. Conflicts: compare each with the other rules of "
               "CLAUDE.md (each has an id, \"<!-- R-001 -->\"): a rule that contradicts or repeats another is updated "
               "or merged with it; propose it, the user decides with you.\n2. Scripts: could a script or hook check "
               "or do it instead of you (file names, formats, limits, line counts, commit messages, forbidden paths, "
               "git steps)? Look at what the project already has. Each fitting one is a proposal, not work: `%(sg)s idea "
               "add \"Script instead of a rule: <rule> -> <what the script does>\" --source rule-review --rule "
               "<R-id when it has one>`. The rule stays in CLAUDE.md until that script's task is done and verified.\n"
               "3. A line that is state or a task, not a rule, belongs in a task: say so; with the user's yes move "
               "it.\nList 0-3 for the user in one numbered list, then `%(sg)s rules reviewed` (it gives new lines their "
               "ids).")


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
