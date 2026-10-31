"""Session Guard: Claude Code hook script (SessionStart, UserPromptSubmit, PostToolUse).

Claude cannot see its own context size or how long the user was away. This
script measures both from the session transcript and tells Claude
(additionalContext) and the user (systemMessage) what to do at each threshold.

- Context: input tokens of the last main-agent reply in the transcript
  (input + cache_creation + cache_read). A measurement, not an estimate.
- Gap: time since the last main-agent reply.
- State: ~/.claude/session_guard/<session_id>.json (long mode, lock, notified
  thresholds, recent message ids). When the app reopens a conversation under a
  new session id, the state is found again through the shared message ids.
- A locked session never passes a prompt to Claude (decision: block), so it
  costs nothing.
- Setup gate: Session Guard stays inactive until the user approved removing the
  rules, skills, scripts and hooks that conflict with it (skill setup). The
  approval is kept in ~/.claude/session_guard/config/setup.json (plugin) or in
  <repo>/.claude/session-guard/setup.json (project copy, cloud). A project is
  reviewed once; a project with nothing to review is approved on its own.
- Project files: in an active project the missing CLAUDE.md, docs/STATUS.md and
  docs/STATUS_ARCHIVE.md are created at session start. Existing files (any
  letter case, or .claude/CLAUDE.md) are never touched.
- General rules: kept in a marked block of ~/.claude/CLAUDE.md (plugin); the
  project copy has no global file, so it gives them at session start.

Other modes:
    session_guard.py --report [project_dir]   one-line usage summary of the
                                              latest session of a project
    session_guard.py --scan [project_dir]     what a conflict review must read
                                              (global + every known project)
    session_guard.py --install-global         writes the general rules block
    session_guard.py --setup approved|declined [--all | project_dir...]
    session_guard.py --print-rules            the rules a review compares with
    session_guard.py --project                project-copy mode (cloud); does
                                              nothing when the plugin is installed
On any error the hook exits 0 silently: it must never break the user's work.
"""
import glob
import json
import os
import re
import shutil
import sys
import time
from datetime import datetime

K = 1000
NORMAL_LIMIT, LONG_LIMIT, BREAK_CONTEXT, BREAK_SECONDS = 400 * K, 900 * K, 125 * K, 3600
HOME = os.path.expanduser("~")
CLAUDE_DIR = os.path.join(HOME, ".claude")
GLOBAL_MD = os.path.join(CLAUDE_DIR, "CLAUDE.md")
STATE_DIR = os.path.join(CLAUDE_DIR, "session_guard")
CONFIG = os.path.join(STATE_DIR, "config", "setup.json")   # in a subfolder: the 30-day state cleanup never sees it
TAIL_BYTES = 4 * 1024 * 1024    # only the last 4 MB of a transcript is read
PENDING_MAX = 24 * 3600         # a background job with no finish notice counts as running this long
STATE_MAX_AGE = 30 * 86400
KEEP_IDS = 10                   # recent assistant message ids kept to recognise a reopened conversation
RULES_VERSION = 2               # raise when a rule change needs a new conflict review (1 = v1.0.0)
SELF = os.path.abspath(__file__)
PROJECT_MODE = False            # set by --project (copy inside a repository, for cloud sessions)

# Commands count only at the start of the message (followed by punctuation or
# the end) or at its end; inside a sentence or a quote they are ignored.
FINISH = r"finish\s+session"
LONG = r"start\s+long\s+mode"

# (threshold, key, instruction for Claude, message for the user, hard stop)
NORMAL_STEPS = [
    (400 * K, "n400", "HARD LIMIT (400k). Do no work. Tell the user the session is closed and to open a new one.",
     "Context 400k: hard limit. Session closed, open a new session.", True),
    (350 * K, "n350", "TARGET STOP (350k). Start no new task. Finish the hand-off if not done (skill session-guard). Tell the user to open a new session.",
     "Context 350k: target stop. Opening a new session is recommended.", False),
    (300 * K, "n300", "HAND-OFF (300k). Prepare the hand-off now with skill session-guard. From now on start no new task; only answer information questions and update the hand-off note on request.",
     "Context 300k: preparing hand-off. No new tasks from now on.", False),
    (250 * K, "n250", "NOTICE (250k). Tell the user the context passed 250k.",
     "Context passed 250k.", False),
]
LONG_STEPS = [
    (900 * K, "l900", "HARD LIMIT (900k, long mode). Do no work. Tell the user the session is closed.",
     "Context 900k: long-mode hard limit. Session closed, open a new session.", True),
    (800 * K, "l800", "NO WORK (800k, long mode). Do no new work. Only answer simple, specific information questions. Prepare the final hand-off note (skill session-guard) and finish it before 900k.",
     "Context 800k: work stopped. Information questions only; hand-off will be finished before 900k.", False),
    (700 * K, "l700", "NOTICE + PREPARE (700k, long mode). Tell the user. Make current work hand-off ready (hand-off note, skill session-guard). You may keep working and take new tasks.",
     "Context 700k: making work hand-off ready.", False),
    (300 * K, "l300", "NOTICE (300k, long mode). Only tell the user; no hand-off.",
     "Context passed 300k (long mode: no hand-off).", False),
    (250 * K, "l250", "NOTICE (250k, long mode). Only tell the user; no hand-off.",
     "Context passed 250k (long mode).", False),
]
# Pure notices are given once; the restrictions from 300k on repeat on every prompt.
NOTIFY_ONCE = {"n250", "l250", "l300", "l700"}

# Something active for over 24 hours (PENDING_MAX) is probably a bug: the user is told, not left waiting.
STALE_JOBS = ("WARNING (24 hours): %d background job(s) or subagent(s) started over 24 hours ago never reported back. "
              "First tell the user in one line: a bug may have occurred, something has been active for over 24 hours, "
              "please look into it. Check the job if you can. If you think it is stuck, suggest ending the session "
              "with the command \"finish session\".")
LONG_TURN = ("WARNING (24 hours): you have been working on the same user message for over 24 hours. Tell the user: a "
             "bug may have occurred, the agent has been active for 24 hours, please look into it. If you think there "
             "is a problem, suggest ending the session with the command \"finish session\".")
WATCH_WAKE = ("[Session Guard] 24-HOUR WATCH: 24 hours have passed since the user's last message and the session is "
              "still busy (work or a background job has not finished). Tell the user: a bug may have occurred, the "
              "agent has been active for 24 hours, please look into it. If you think there is a problem, suggest "
              "ending the session with the command \"finish session\".")
WATCH_SECONDS = int(os.environ.get("SESSION_GUARD_WATCH_SECONDS") or 24 * 3600)  # tests shorten it
WATCH_STEP = 60                 # a watch checks once a minute whether a newer prompt replaced it (local file read, no Claude request)
STALE_USER = ("possible bug: something has been active for over 24 hours. Please look into it; if it is stuck, "
              "end the session with \"finish session\".")

RULES = """[Session Guard] Rules for this session (plugin session-guard).
- You cannot measure context size or time between messages. Session Guard measures them itself and writes to
  you ("[Session Guard] Context: ...") only when it has an instruction. Follow it. Quote its numbers; never
  invent one. Never add context size or mode to your replies on your own.
- Session start: docs/STATUS.md (if any) is given below; do not read it again. Read docs/STATUS_ARCHIVE.md only
  when needed, and then only the matching part via search.
- Normal mode: 250k notice; 300k hand-off, then no new tasks (only information questions and hand-off note
  updates on request); 350k target stop; 400k hard stop.
- Long mode ("start long mode"; this session only, cannot be switched off): 250k/300k notice only; 700k notice and
  make work hand-off ready; 800k no work, only simple specific questions, final hand-off note; 900k hard stop.
  The break rule does not apply.
- Break (normal mode): gap > 1 hour and context > 125k -> do NOT run the user's command and do no reading or
  research for it; do the hand-off and copy the command into the note. Context <= 125k -> remind, then continue.
  Waiting for your own background job is not a break.
- "finish session" (any mode): only hand-off and session measurement, nothing else.
- Commands ("start long mode", "finish session") count only at the end of the message, or at its start followed
  by a comma or period.
- After a hard stop, a break hand-off or "finish session" Session Guard locks the session: prompts never reach you.
- Procedures: skill session-guard.
- Reply style (saves tokens): plain words, short sentences, no needless detail; the user asks when they want more.
  Give each issue its own number. Under it, in this order and only those needed, the labels PROBLEM /
  RECOMMENDATION / ASSUMPTION / WARNING / UNVERIFIED, translated into the user's language.
- Facts: never present as fact anything you have not researched or checked, or are not sure of; label it
  UNVERIFIED. Label your own recommendations and assumptions RECOMMENDATION / ASSUMPTION, so the user knows
  they are yours and not established facts.
- Always answer in the user's language."""

# Kept between these markers in ~/.claude/CLAUDE.md; the project copy gives it at session start instead.
BLOCK_BEGIN = "<!-- session-guard:begin (managed by the Session Guard plugin; changes inside this block are overwritten) -->"
BLOCK_END = "<!-- session-guard:end -->"
GENERAL_RULES = """# Session Guard general rules
- These rules and the "[Session Guard] Rules" given at session start win over any other CLAUDE.md rule on the
  same topic, project files included. On other topics a project CLAUDE.md wins over the global one.

## Project files
- Every project has `CLAUDE.md` (permanent facts and decisions), `docs/STATUS.md` (current state) and
  `docs/STATUS_ARCHIVE.md` (history). Session Guard creates the missing ones; it never overwrites existing ones.
- STATUS.md fits on one screen and is not a log: update the existing sections, merge repeats, add no dated lines.
  Unless the project CLAUDE.md sets its own content: current goal, open issues, next step, running jobs and how
  to check them, and a "Hand-off note" section (procedure: skill session-guard).
- A line leaving STATUS that will matter later (decision, measurement, proven cause, mistake not to repeat, how to
  undo a change) goes as one dated line to STATUS_ARCHIVE.md under the matching heading; otherwise it is deleted.
  The archive is not read every session; find the heading by search. No second archive, no separate hand-off file.
- Update STATUS when a step ends, before long work, on an error, while waiting for the user, before a break.

## Topics
- Suggest a new conversation when the topic changes, whatever the token count. Do not add unrelated small work
  to a grown conversation.

## Tools and permissions
- Ask for screen, app and similar permissions right before the action that needs them, not at session start.
  Before long unattended work, ask before the user leaves.
- Load deferred tools one by one as needed (ToolSearch with `select:`), never in bulk.

## Session measurement
- At session start and on "finish session", read the weekly and 5-hour quota percentages if the app has a usage
  tool. On "finish session" also run the `--report` command Session Guard gives and add one row to the "Session
  measurements" table of STATUS_ARCHIVE.md. A value you cannot read is "n/a"; never invent a number.
- The quota formula is unknown: never present a token drop as a quota saving; compare measured percentages."""

PROJECT_TEMPLATE = """# %s

Permanent project facts and decisions. General rules: Session Guard.

## Project
- Goal:

## Decisions

## Working style
"""
STATUS_TEMPLATE = """# STATUS

## Current goal
-

## Open issues
- None

## Next step
-

## Hand-off note
- None yet
"""
ARCHIVE_TEMPLATE = """# STATUS archive

One dated line per item. Not read every session: find the heading by search.

## Decisions and measurements

## Session measurements
| Date | Requests | Cache read | Cache write | Output | Largest context | Weekly % | 5-hour % |
|---|---|---|---|---|---|---|---|
"""

SETUP_NEEDED = ("[Session Guard] INACTIVE: its one-time setup is not done, or its rules changed since then. Until the "
                "user approves the setup, Session Guard measures nothing, enforces no threshold and locks nothing; "
                "do not apply its rules. In your first reply tell the user this in one line and ask whether to run "
                "the setup now (skill %s). Start it only on the user's yes. Script command: %s")
REVIEW_NEEDED = ("[Session Guard] INACTIVE in this project (%s): its CLAUDE.md, skills, commands or hooks were not yet "
                 "checked for rules that conflict with Session Guard. Do not apply Session Guard rules here yet. In "
                 "your first reply tell the user this in one line and ask whether to run the project review now "
                 "(skill %s, \"Project review\"). Start it only on the user's yes. Script command: %s")
DECLINED = ("[Session Guard] OFF%s: the user kept rules that conflict with it. Do not apply Session Guard rules. "
            "If the user asks, the review can be run again (skill %s).")


def normalize(text):
    # Commands are English; "FINISH" on a Turkish keyboard lowercases to "fınısh", so ı/İ count as i.
    return text.replace("İ", "i").lower().replace("ı", "i")


def has_command(pattern, text):
    return bool(re.search(r"^[\W_]*" + pattern + r"(?:[.,!;:\n]|\s*$)", text) or re.search(pattern + r"[\W_]*$", text))


def to_time(stamp):
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()
    except (AttributeError, ValueError):
        return None


def is_prompt(entry, content):
    """A message the user typed (not a tool result, task notice, subagent message or app reminder)."""
    if entry.get("type") != "user" or entry.get("isMeta"):
        return False
    raw = entry["message"].get("content")
    if isinstance(raw, str):
        return not raw.lstrip().startswith("<")
    return any(isinstance(p, dict) and p.get("type") == "text" and not (p.get("text") or "").lstrip().startswith("<")
               for p in content) and not any(isinstance(p, dict) and p.get("type") == "tool_result" for p in content)


def read_transcript(path):
    """Returns (context, last_reply_time, assistant_ids, pending_jobs, stale_jobs, last_prompt_time, turn_open)
    from the transcript tail. Stale: a background job or subagent with no finish notice after 24 hours.
    turn_open: the last entry is not Claude's final text reply (a tool runs, or the prompt got no answer)."""
    if not path or not os.path.exists(path):
        return None, None, [], 0, 0, None, False
    with open(path, "rb") as fh:
        fh.seek(max(0, os.path.getsize(path) - TAIL_BYTES))
        lines = fh.read().decode("utf-8", errors="replace").splitlines()
    context = last = prompt_at = last_kind = None
    ids, launched, agent_of, done = [], {}, {}, set()
    for line in lines:
        # Finish notices: background commands (<task-notification>) and subagents (<agent-message>).
        if "<task-notification>" in line or "agent-message" in line:
            done.update(re.findall(r"<tool-use-id>(\w+)</tool-use-id>", line))
            done.update(re.findall(r"<task-id>(\w+)</task-id>", line))
            done.update(re.findall(r'agent-message from=\\*"(\w+)', line))
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        msg = entry.get("message")
        if not isinstance(msg, dict) or entry.get("isSidechain"):
            continue
        content = msg.get("content") if isinstance(msg.get("content"), list) else []
        if entry.get("type") == "assistant":
            last_kind = "tool" if any(isinstance(p, dict) and p.get("type") == "tool_use" for p in content) else "text"
            if msg.get("id") and msg.get("id") not in ids:
                ids.append(msg["id"])
            if msg.get("usage"):
                u = msg["usage"]
                context = (u.get("input_tokens") or 0) + (u.get("cache_creation_input_tokens") or 0) + (u.get("cache_read_input_tokens") or 0)
                last = to_time(entry.get("timestamp")) or last
            for part in content:
                if isinstance(part, dict) and part.get("type") == "tool_use":
                    args = part.get("input") or {}
                    if args.get("run_in_background") is True or part.get("name") == "Agent":
                        launched[part.get("id")] = to_time(entry.get("timestamp")) or time.time()
        elif entry.get("type") == "user":
            if is_prompt(entry, content):
                prompt_at = to_time(entry.get("timestamp")) or prompt_at
                last_kind = "prompt"
            elif any(isinstance(p, dict) and p.get("type") == "tool_result" for p in content):
                last_kind = "result"
            for part in content:
                if isinstance(part, dict) and part.get("type") == "tool_result" and part.get("tool_use_id") in launched:
                    found = re.search(r"agentId: (\w+)", json.dumps(part.get("content")))
                    if found:
                        agent_of[part["tool_use_id"]] = found.group(1)
    now = time.time()
    open_jobs = [at for tid, at in launched.items() if tid not in done and agent_of.get(tid) not in done]
    pending = sum(1 for at in open_jobs if now - at < PENDING_MAX)
    return context, last, ids, pending, len(open_jobs) - pending, prompt_at, last_kind in ("tool", "result", "prompt")


def state_path(sid):
    return os.path.join(STATE_DIR, re.sub(r"[^A-Za-z0-9-]", "_", sid or "none") + ".json")


def load_state(sid, ids):
    try:
        with open(state_path(sid), encoding="utf-8") as fh:
            return fix_state(json.load(fh))
    except (OSError, ValueError):
        pass
    # Reopened conversation under a new id: inherit the state that shares message ids.
    if ids and os.path.isdir(STATE_DIR):
        known = set(ids)
        for path in glob.glob(os.path.join(STATE_DIR, "*.json")):
            try:
                with open(path, encoding="utf-8") as fh:
                    old = json.load(fh)
            except (OSError, ValueError):
                continue
            if known & set(old.get("ids") or []):
                return fix_state(old)
    return fix_state({})


def fix_state(state):
    """Fills missing keys, so a damaged or older state file never silences the guard."""
    if not isinstance(state, dict):
        state = {}
    for key, value in (("long", False), ("locked", False), ("notified", []), ("ids", [])):
        state.setdefault(key, value)
    return state


def save_state(sid, state, ids):
    if ids:
        state["ids"] = ids[-KEEP_IDS:]
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(state_path(sid), "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False)


def step_for(context, long_mode):
    for step in (LONG_STEPS if long_mode else NORMAL_STEPS):
        if context is not None and context >= step[0]:
            return step
    return None


def k(n):
    return "unknown" if n is None else "%dk" % round(n / K)


def emit(data):
    sys.stdout.write(json.dumps(data, ensure_ascii=True))
    return 0


def out(text):
    """Prints UTF-8 whatever the console code page is (file paths and CLAUDE.md lines may be non-ASCII)."""
    sys.stdout.buffer.write((text + "\n").encode("utf-8"))
    sys.stdout.flush()


# --- projects, setup gate, project files ------------------------------------
def key(path):
    return os.path.normcase(os.path.abspath(path))


def inside(path, parent):
    p, q = key(path), key(parent)
    return p == q or p.startswith(q.rstrip("\\/") + os.sep)


def usable_root(path):
    """The folder as a project root, or None for home, a drive root, ~/.claude and system folders."""
    if not path or not os.path.isdir(path):
        return None
    path = os.path.abspath(path)
    if key(path) == key(HOME) or os.path.dirname(path) == path or inside(path, CLAUDE_DIR):
        return None
    for name in ("SystemRoot", "ProgramFiles", "ProgramFiles(x86)", "ProgramData"):
        if os.environ.get(name) and inside(path, os.environ[name]):
            return None
    return path


def project_root(inp):
    """CLAUDE_PROJECT_DIR (where the session started); without it, the nearest folder up with CLAUDE.md or .git."""
    if os.environ.get("CLAUDE_PROJECT_DIR"):
        return usable_root(os.environ["CLAUDE_PROJECT_DIR"])
    start = os.path.abspath(inp.get("cwd") or os.getcwd())
    path = start
    while usable_root(path):
        if exists_ci(path, "CLAUDE.md") or os.path.exists(os.path.join(path, ".git")):
            return path
        path = os.path.dirname(path)
    return usable_root(start)


def main_checkout(root):
    """A git worktree (.git is a file: "gitdir: <main>/.git/worktrees/<name>") shares the review of its main
    checkout: same repository, same CLAUDE.md. Anything else is its own main checkout."""
    try:
        with open(os.path.join(root, ".git"), encoding="utf-8") as fh:
            found = re.match(r"gitdir:\s*(.+)", fh.read().strip())
    except OSError:
        return root
    if found:
        gitdir = os.path.abspath(os.path.join(root, found.group(1).strip()))
        if os.path.basename(os.path.dirname(gitdir)) == "worktrees" and os.path.isdir(os.path.dirname(os.path.dirname(gitdir))):
            return os.path.dirname(os.path.dirname(os.path.dirname(gitdir)))
    return root


def exists_ci(base, rel):
    """The real path of base/rel matched without regard to letter case, or None."""
    path = base
    for part in rel.replace("\\", "/").split("/"):
        try:
            names = os.listdir(path)
        except OSError:
            return None
        match = [n for n in names if n.lower() == part.lower()]
        if not match:
            return None
        path = os.path.join(path, part if part in match else match[0])
    return path


def settings_hooks(path):
    """Hook commands of a settings file, Session Guard's own excluded."""
    try:
        with open(path, encoding="utf-8-sig") as fh:
            hooks = json.load(fh).get("hooks") or {}
    except (OSError, ValueError, AttributeError):
        return []
    return ["%s: %s" % (event, h.get("command") or h.get("type")) for event, groups in hooks.items()
            if isinstance(groups, list) for g in groups if isinstance(g, dict)
            for h in g.get("hooks", []) if isinstance(h, dict) and "session_guard.py" not in (h.get("command") or "")]


def review_items(root, is_global=False):
    """What a conflict review must read: CLAUDE.md files, skills, commands, agents, scripts, hook settings."""
    if is_global:
        claude, files = CLAUDE_DIR, [GLOBAL_MD]
        folders = ("skills", "commands", "agents", "scripts", "output-styles", "rules")
    else:
        claude = os.path.join(root, ".claude")
        files = [exists_ci(root, n) for n in ("CLAUDE.md", "CLAUDE.local.md", ".claude/CLAUDE.md")]
        folders = ("skills", "commands", "agents", "output-styles", "rules")
    items = [f for f in files if f and os.path.isfile(f)]
    if is_global:  # auto-memory files are read at session start too
        items += sorted(glob.glob(os.path.join(CLAUDE_DIR, "projects", "*", "memory", "*.md")))
    for folder in folders:
        path = os.path.join(claude, folder)
        if os.path.isdir(path):
            # The project copy's own skills are Session Guard; an old copy in ~/.claude is a conflict to find.
            items += [os.path.join(path, n) for n in sorted(os.listdir(path))
                      if is_global or not n.startswith("session-guard")]
    items += [os.path.join(claude, n) for n in ("settings.json", "settings.local.json")
              if settings_hooks(os.path.join(claude, n))]
    return items


def load_config():
    try:
        with open(CONFIG, encoding="utf-8") as fh:
            cfg = json.load(fh)
    except (OSError, ValueError):
        cfg = {}
    if not isinstance(cfg, dict):
        cfg = {}
    if not isinstance(cfg.get("projects"), dict):
        cfg["projects"] = {}
    return cfg


def save_config(cfg):
    os.makedirs(os.path.dirname(CONFIG), exist_ok=True)
    with open(CONFIG, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False, indent=1)


def marker_path(root):
    return os.path.join(root, ".claude", "session-guard", "setup.json")


def write_marker(root, status):
    os.makedirs(os.path.dirname(marker_path(root)), exist_ok=True)
    with open(marker_path(root), "w", encoding="utf-8") as fh:
        json.dump({"rules": RULES_VERSION, "status": status, "date": datetime.now().strftime("%Y-%m-%d")}, fh)
        fh.write("\n")


def activation(root):
    """'active', 'setup' (one-time setup missing or rules changed), 'review' (project not checked) or 'declined'."""
    if PROJECT_MODE:
        if root is None:
            return "active"
        try:
            with open(marker_path(root), encoding="utf-8") as fh:
                marker = json.load(fh)
        except (OSError, ValueError):
            marker = {}
        if not isinstance(marker, dict):
            marker = {}
        if marker.get("status") == "declined":
            return "declined"
        if marker.get("status") == "approved" and marker.get("rules") == RULES_VERSION:
            return "active"
        if review_items(root):
            return "review"
        write_marker(root, "approved")
        return "active"
    cfg = load_config()
    if cfg.get("rules") != RULES_VERSION or cfg.get("global") not in ("approved", "declined"):
        return "setup"
    if cfg["global"] == "declined":
        return "declined"
    if root is None:
        return "active"
    status = cfg["projects"].get(key(main_checkout(root)))
    if status:
        return "active" if status == "approved" else "declined"
    if review_items(root):
        return "review"
    cfg["projects"][key(main_checkout(root))] = "approved"  # nothing to review: a new or empty project
    save_config(cfg)
    return "active"


def command():
    return '%s "%s"%s' % ("python" if os.name == "nt" else "python3", SELF, " --project" if PROJECT_MODE else "")


def inactive(state, root):
    skill = "session-guard-setup" if PROJECT_MODE else "session-guard:setup"
    if state == "setup":
        ctx, user = SETUP_NEEDED % (skill, command()), "inactive until its one-time setup is done (/%s)." % skill
    elif state == "review":
        ctx, user = REVIEW_NEEDED % (root, skill, command()), "inactive in this project until its rules are reviewed (/%s)." % skill
    else:
        ctx, user = DECLINED % (" in this project" if root else "", skill), "off (conflicting rules were kept)."
    return {"systemMessage": "Session Guard " + user,
            "hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": ctx}}


def create_project_files(root):
    """Creates the missing CLAUDE.md, docs/STATUS.md, docs/STATUS_ARCHIVE.md. Existing files are never touched."""
    made = []
    wanted = [("docs/STATUS.md", STATUS_TEMPLATE), ("docs/STATUS_ARCHIVE.md", ARCHIVE_TEMPLATE)]
    if not (exists_ci(root, "CLAUDE.md") or exists_ci(root, ".claude/CLAUDE.md")):
        wanted.insert(0, ("CLAUDE.md", PROJECT_TEMPLATE % os.path.basename(root)))
    for rel, text in wanted:
        if exists_ci(root, rel):
            continue
        path = os.path.join(root, *rel.split("/"))
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "x", encoding="utf-8") as fh:  # "x": never overwrites, even in a race
                fh.write(text)
            made.append(rel)
        except OSError:
            pass
    return made


def ensure_global_block():
    """Writes GENERAL_RULES between the markers of ~/.claude/CLAUDE.md (created if missing); the user's own text
    is kept. Returns 'same', 'updated', 'added' or 'broken' (markers damaged: the file is left alone)."""
    try:
        with open(GLOBAL_MD, encoding="utf-8-sig", newline="") as fh:
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
    os.makedirs(CLAUDE_DIR, exist_ok=True)
    with open(GLOBAL_MD, "w", encoding="utf-8", newline="") as fh:
        fh.write(new)
    return result


def clean_states():
    """Deletes Session Guard's own per-session records (long mode, lock, notices) unused for 30 days, so the
    reopened-conversation search stays fast. A locked session's record is kept: a reopened old session stays
    locked. Nothing else is ever deleted here (no project, conversation or setup file)."""
    for path in glob.glob(os.path.join(STATE_DIR, "*.json")):
        if time.time() - os.path.getmtime(path) <= STATE_MAX_AGE:
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                locked = fix_state(json.load(fh)).get("locked")
        except (OSError, ValueError):
            locked = False
        if not locked:
            os.remove(path)


def on_session_start(inp):
    if os.path.isdir(STATE_DIR):
        clean_states()
    if inp.get("source") == "resume":
        return 0
    root = project_root(inp)
    state = activation(root)
    if state != "active":
        return emit(inactive(state, root))
    parts = [RULES]
    if PROJECT_MODE:
        parts.append(GENERAL_RULES)  # no global CLAUDE.md here (cloud)
    else:
        block = ensure_global_block()
        if block in ("added", "updated"):
            parts.append("The Session Guard block of ~/.claude/CLAUDE.md was refreshed; it reads:\n" + GENERAL_RULES)
        elif block == "broken":
            parts.append("WARNING: the session-guard markers in ~/.claude/CLAUDE.md are damaged, so the general rules "
                         "were not refreshed. Tell the user; the setup (skill session-guard:setup) repairs it.")
    # A worktree gets the files from its main checkout through git, never a second, diverging copy.
    made = create_project_files(root) if root and main_checkout(root) == root else []
    if made:
        parts.append("Session Guard created %s in %s (missing files only; existing files are never touched)%s." % (
            ", ".join(made), root, ". Commit them with your work" if PROJECT_MODE else ""))
    status = exists_ci(root, "docs/STATUS.md") if root else None
    if status:
        with open(status, encoding="utf-8", errors="replace") as fh:
            parts.append("docs/STATUS.md (already loaded, do not read again):\n\n" + fh.read()[:8000])
    elif root is None:
        parts.append("Not in a project folder (home, drive root, ~/.claude or a system folder): no project files.")
    return emit({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": "\n\n".join(parts)}})


def on_prompt(inp):
    if activation(project_root(inp)) != "active":
        return 0
    sid = inp.get("session_id")
    context, last, ids, pending, stale, _, _ = read_transcript(inp.get("transcript_path"))
    state = load_state(sid, ids)
    if state.get("locked"):
        save_state(sid, state, ids)
        return emit({"decision": "block", "reason": "Session Guard: session closed (%s). Open a new session." % state.get("reason", "")})

    prompt = normalize(inp.get("prompt") or "")
    gap = time.time() - last if last else None
    notes, user = [], []
    if has_command(LONG, prompt) and not state.get("long"):
        state["long"] = True
        notes.append("LONG MODE is on for this session (cannot be switched off). Confirm it to the user in one line.")
    long_mode = state.get("long")
    finish = has_command(FINISH, prompt)
    # The status line goes out only together with an instruction: a plain prompt costs no tokens.
    line = "[Session Guard] Context: %s (last reply). Mode: %s. Since last reply: %s.%s" % (
        k(context), "LONG" if long_mode else "normal", "unknown" if gap is None else "%d min" % (gap // 60),
        " Background jobs still running: %d (break rule off)." % pending if pending else "")

    limit = LONG_LIMIT if long_mode else NORMAL_LIMIT
    if context is not None and context >= limit and not finish:
        state.update(locked=True, reason="context %s" % k(context))
        save_state(sid, state, ids)
        return emit({"decision": "block", "reason": "Session Guard: context %s, hard limit. Session closed, open a new session." % k(context)})

    if finish:
        state.update(locked=True, reason="finish session")
        notes.append('FINISH SESSION. Do nothing else the message asks. Only follow skill session-guard "Finish session" '
                      '(hand-off + measurement: python "%s" --report). The session is locked after this reply.' % SELF)
        user.append("Finishing session: preparing hand-off. Later messages will not be delivered.")
    elif not long_mode and not pending and gap is not None and gap > BREAK_SECONDS:
        if context is not None and context > BREAK_CONTEXT:
            state.update(locked=True, reason="break over 1 hour, context %s" % k(context))
            notes.append("BREAK RULE (gap %d min, context %s > 125k). Do NOT run the user's command and do no reading or "
                         'research for it. Only follow skill session-guard "Break hand-off"; copy the command into the note '
                         "word for word; tell the user to continue in a new session. The session is locked after this reply."
                         % (gap // 60, k(context)))
            user.append("Break over 1 hour and context %s: command not run, it goes into the hand-off note." % k(context))
        else:
            notes.append("BREAK REMINDER: gap over 1 hour (%d min), context %s <= 125k. Remind the user in one line, then "
                         "handle the command normally." % (gap // 60, k(context)))
            user.append("Break over 1 hour; context %s, continuing in this session." % k(context))

    if stale and not state.get("locked"):
        notes.append(STALE_JOBS % stale)
        if "stale24" not in state["notified"]:
            state["notified"].append("stale24")
            user.append(STALE_USER)

    if not state.get("locked"):
        step = step_for(context, long_mode)
        if step:
            if step[1] not in NOTIFY_ONCE or step[1] not in state["notified"]:
                notes.append(step[2])
            if step[1] not in state["notified"]:
                state["notified"].append(step[1])
                user.append(step[3])

    save_state(sid, state, ids)
    if not notes:
        return 0
    data = {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": "\n".join([line] + notes)}}
    if user:
        data["systemMessage"] = "Session Guard: " + " ".join(user)
    return emit(data)


def watch(inp):
    """24-hour watch: an async hook (asyncRewake) started with every prompt. The newest prompt's watch replaces the
    older ones (they see a new token within a minute and exit). After 24 hours with no newer prompt, if the
    session is still busy (a tool running, a background job open, or no answer to the prompt), exit code 2 wakes
    Claude with WATCH_WAKE, so the user is told even when no other hook event happens. A finished turn waiting
    for the user is not a bug: then it exits 0 and costs nothing."""
    sid = inp.get("session_id")
    token_path = os.path.join(STATE_DIR, "watch", re.sub(r"[^A-Za-z0-9-]", "_", sid or "none"))
    token = "%f %d" % (time.time(), os.getpid())
    os.makedirs(os.path.dirname(token_path), exist_ok=True)
    with open(token_path, "w", encoding="utf-8") as fh:
        fh.write(token)
    deadline = time.time() + WATCH_SECONDS
    while time.time() < deadline:
        time.sleep(max(0, min(WATCH_STEP, deadline - time.time())))
        try:
            with open(token_path, encoding="utf-8") as fh:
                if fh.read() != token:
                    return 0  # a newer prompt started its own watch
        except OSError:
            return 0
    if activation(project_root(inp)) != "active":
        return 0
    _, _, ids, pending, stale, _, turn_open = read_transcript(inp.get("transcript_path"))
    if load_state(sid, ids).get("locked") or not (pending or stale or turn_open):
        return 0
    sys.stderr.write(WATCH_WAKE)
    return 2


def on_tool(inp):
    """While Claude works: tells once when a new threshold is passed; stops the turn at the hard limit."""
    if activation(project_root(inp)) != "active":
        return 0
    sid = inp.get("session_id")
    context, _, ids, _, _, prompt_at, _ = read_transcript(inp.get("transcript_path"))
    state = load_state(sid, ids)
    if state.get("locked"):
        return 0
    turn = "turn24:%s" % prompt_at
    if prompt_at and time.time() - prompt_at > PENDING_MAX and turn not in state["notified"]:
        state["notified"].append(turn)  # once per user message
        save_state(sid, state, ids)
        return emit({"systemMessage": "Session Guard: " + STALE_USER,
                     "hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": LONG_TURN}})
    step = step_for(context, state.get("long"))
    if not step or step[1] in state["notified"]:
        return 0
    state["notified"].append(step[1])
    data = {"systemMessage": "Session Guard: " + step[3],
            "hookSpecificOutput": {"hookEventName": "PostToolUse",
                                   "additionalContext": "[Session Guard] Context: %s (during work). %s" % (k(context), step[2])}}
    if step[4]:
        state.update(locked=True, reason="context %s" % k(context))
        data.update({"continue": False, "stopReason": step[3]})
    save_state(sid, state, ids)
    return emit(data)


def report(project_dir):
    """One line: requests, cache read/write, output tokens, largest context of the latest session."""
    cwd = os.path.abspath(project_dir or os.getcwd())
    folder = os.path.join(os.path.expanduser("~"), ".claude", "projects", re.sub(r"[^A-Za-z0-9]", "-", cwd))
    files = glob.glob(os.path.join(folder, "*.jsonl"))
    if not files:
        print("no transcript: " + folder)
        return 1
    path = max(files, key=os.path.getmtime)
    usage, order = {}, []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            msg = entry.get("message") or {}
            if entry.get("type") == "assistant" and isinstance(msg, dict) and msg.get("id") and msg.get("usage"):
                if msg["id"] not in usage:
                    order.append(msg["id"])
                usage[msg["id"]] = msg["usage"]  # the last record holds the final value
    read = write = out = biggest = 0
    for mid in order:
        u = usage[mid]
        r, w = u.get("cache_read_input_tokens") or 0, u.get("cache_creation_input_tokens") or 0
        read, write, out = read + r, write + w, out + (u.get("output_tokens") or 0)
        biggest = max(biggest, r + w + (u.get("input_tokens") or 0))
    print("session=%s requests=%d cache_read=%d cache_write=%d output=%d largest_context=%d" % (
        os.path.basename(path)[:8], len(order), read, write, out, biggest))
    return 0


# --- setup commands (run by skill setup) -------------------------------------
KEYWORDS = re.compile(r"context|bağlam|baglam|token|\b\d{2,3}\s?k\b|hand-?off|devir|mola|\bbreak\b|finish|long mode|"
                      r"uzun mod|status|archive|arşiv|arsiv|session|oturum|threshold|eşik|limit|sınır|compact|"
                      r"reply style|yazım|label|etiket", re.I)
TEXT_EXT = (".md", ".py", ".ps1", ".sh", ".js", ".ts", ".json", ".txt", ".bat", ".cmd")
MAX_HITS = 40


def first_cwd(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for _, line in zip(range(200), fh):
                try:
                    cwd = json.loads(line).get("cwd")
                except (ValueError, AttributeError):
                    continue
                if cwd:
                    return cwd
    except OSError:
        pass
    return None


def known_projects():
    """Every project folder Claude Code has sessions for (the "cwd" its transcripts start with)."""
    roots = {}
    for folder in glob.glob(os.path.join(CLAUDE_DIR, "projects", "*")):
        for path in sorted(glob.glob(os.path.join(folder, "*.jsonl")), key=os.path.getmtime, reverse=True)[:3]:
            cwd = first_cwd(path)
            if cwd:
                root = usable_root(cwd)
                if root:
                    root = main_checkout(root)
                    roots[key(root)] = root
                break
    return sorted(roots.values(), key=str.lower)


def hits(path):
    """Lines that touch Session Guard topics; text outside the Session Guard block only."""
    if os.path.isfile(path) and os.path.basename(path).lower() in ("settings.json", "settings.local.json"):
        return settings_hooks(path)
    files = [path]
    if os.path.isdir(path):
        files = sorted(os.path.join(d, n) for d, _, names in os.walk(path) for n in names if n.lower().endswith(TEXT_EXT))
    found = []
    for name in files[:30]:
        try:
            with open(name, encoding="utf-8", errors="replace") as fh:
                lines = fh.read().splitlines()
        except OSError:
            continue
        inside_block = False
        for n, line in enumerate(lines, 1):
            if line.startswith(BLOCK_BEGIN.split(" (")[0]):
                inside_block = True
            if not inside_block and KEYWORDS.search(line):
                where = os.path.relpath(name, path) + ":" if name != path else ""
                found.append("%s%d: %s" % (where, n, line.strip()[:160]))
            if line.startswith(BLOCK_END):
                inside_block = False
    return found[:MAX_HITS] + (["... more lines: read the file"] if len(found) > MAX_HITS else [])


def block_state():
    try:
        with open(GLOBAL_MD, encoding="utf-8-sig") as fh:
            text = fh.read()
    except OSError:
        return "missing (setup creates it)"
    starts, ends = text.count(BLOCK_BEGIN.split(" (")[0]), text.count(BLOCK_END)
    return {(0, 0): "present, no Session Guard block yet", (1, 1): "present, Session Guard block in place"}.get(
        (starts, ends), "present, Session Guard markers DAMAGED (fix them by hand first)")


def scan(target):
    cfg = load_config()
    if target:
        root = os.path.abspath(target)
        groups = [("PROJECT", root, False)]
    else:
        groups = [("GLOBAL", CLAUDE_DIR, True)] + [("PROJECT", p, False) for p in known_projects()]
    lines = []
    for label, root, is_global in groups:
        if is_global:
            lines.append("GLOBAL %s  [CLAUDE.md: %s]" % (root, block_state()))
        else:
            lines.append("PROJECT %s  [review: %s]" % (root, cfg["projects"].get(key(root), "not done")))
        items = review_items(root, is_global)
        if not items:
            lines.append("  nothing to review")
        for item in items:
            lines.append("  " + item + ("/" if os.path.isdir(item) else ""))
            lines += ["      " + h for h in hits(item)]
    out("\n".join(lines))
    return 0


def record(args):
    """--setup approved|declined [--all | folder...]: no folder = global; --all = global + every known project."""
    status, rest = (args[0] if args else ""), args[1:]
    if status not in ("approved", "declined"):
        out("usage: --setup approved|declined [--all | project_folder ...]")
        return 2
    folders = [os.path.abspath(f) for f in rest if f != "--all"]
    if PROJECT_MODE:
        for root in folders or [os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()]:
            write_marker(root, status)
            out("recorded %s for %s (commit .claude/session-guard/setup.json)" % (status, root))
        return 0
    cfg = load_config()
    if not folders or "--all" in rest:
        cfg.update(rules=RULES_VERSION, date=datetime.now().strftime("%Y-%m-%d"))
        cfg["global"] = status
        out("recorded %s: global" % status)
    if "--all" in rest:
        folders += known_projects()
    for root in folders:
        root = main_checkout(root)
        cfg["projects"][key(root)] = status
        out("recorded %s: %s" % (status, root))
    save_config(cfg)
    return 0


def cli(args):
    if args[0] == "--scan":
        return scan(args[1] if len(args) > 1 else None)
    if args[0] == "--install-global":
        result = ensure_global_block()
        out("global CLAUDE.md (%s): Session Guard block %s" % (GLOBAL_MD, result))
        return 1 if result == "broken" else 0
    if args[0] == "--setup":
        return record(args[1:])
    if args[0] == "--print-rules":
        out(RULES + "\n\n" + BLOCK_BEGIN + "\n" + GENERAL_RULES + "\n" + BLOCK_END)
        return 0
    return 2


def plugin_installed():
    """True when the plugin copy exists under ~/.claude/plugins (then the project copy stays quiet)."""
    pattern = os.path.join(os.path.expanduser("~"), ".claude", "plugins", "**", "session_guard.py")
    return any(os.path.abspath(p) != SELF for p in glob.glob(pattern, recursive=True))


def main():
    global PROJECT_MODE
    args = sys.argv[1:]
    PROJECT_MODE = "--project" in args
    args = [a for a in args if a != "--project"]
    if args[:1] == ["--report"]:
        return report(args[1] if len(args) > 1 else None)
    if args[:1] and args[0] in ("--scan", "--install-global", "--setup", "--print-rules"):
        return cli(args)
    try:
        if PROJECT_MODE and plugin_installed():
            return 0
        inp = json.loads(sys.stdin.buffer.read().decode("utf-8", errors="replace") or "{}")
        event = inp.get("hook_event_name")
        if event == "SessionStart":
            return on_session_start(inp)
        if event == "UserPromptSubmit" and "--watch" in args:
            return watch(inp)
        if event == "UserPromptSubmit":
            return on_prompt(inp)
        if event == "PostToolUse":
            return on_tool(inp)
    except Exception:  # the guard must never break the user's work
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
