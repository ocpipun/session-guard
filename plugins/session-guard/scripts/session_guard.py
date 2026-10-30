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

Other modes:
    session_guard.py --report [project_dir]   one-line usage summary of the
                                              latest session of a project
    session_guard.py --project                project-copy mode (cloud); does
                                              nothing when the plugin is installed
On any error the script exits 0 silently: it must never break the user's work.
"""
import glob
import json
import os
import re
import sys
import time
from datetime import datetime

K = 1000
NORMAL_LIMIT, LONG_LIMIT, BREAK_CONTEXT, BREAK_SECONDS = 400 * K, 900 * K, 125 * K, 3600
STATE_DIR = os.path.join(os.path.expanduser("~"), ".claude", "session_guard")
TAIL_BYTES = 4 * 1024 * 1024    # only the last 4 MB of a transcript is read
PENDING_MAX = 24 * 3600         # a background job with no finish notice counts as running this long
STATE_MAX_AGE = 30 * 86400
KEEP_IDS = 10                   # recent assistant message ids kept to recognise a reopened conversation
SELF = os.path.abspath(__file__)

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

RULES = """[Session Guard] Rules for this session (plugin session-guard).
- You cannot measure context size or time between messages. Session Guard reports them on every prompt
  ("[Session Guard] Context: ...") and may add an instruction. Follow it. Quote its numbers; never invent one.
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
  RECOMMENDATION / ASSUMPTION / WARNING / UNVERIFIED, translated into the user's language. Never present
  anything uncertain as fact; mark your own recommendations and assumptions with their labels.
- Always answer in the user's language."""


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


def read_transcript(path):
    """Returns (context, last_reply_time, assistant_ids, pending_jobs) from the transcript tail."""
    if not path or not os.path.exists(path):
        return None, None, [], 0
    with open(path, "rb") as fh:
        fh.seek(max(0, os.path.getsize(path) - TAIL_BYTES))
        lines = fh.read().decode("utf-8", errors="replace").splitlines()
    context = last = None
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
            for part in content:
                if isinstance(part, dict) and part.get("type") == "tool_result" and part.get("tool_use_id") in launched:
                    found = re.search(r"agentId: (\w+)", json.dumps(part.get("content")))
                    if found:
                        agent_of[part["tool_use_id"]] = found.group(1)
    now = time.time()
    pending = sum(1 for tid, at in launched.items()
                  if tid not in done and agent_of.get(tid) not in done and now - at < PENDING_MAX)
    return context, last, ids, pending


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


def on_session_start(inp):
    if os.path.isdir(STATE_DIR):
        for path in glob.glob(os.path.join(STATE_DIR, "*.json")):
            if time.time() - os.path.getmtime(path) > STATE_MAX_AGE:
                os.remove(path)
    if inp.get("source") == "resume":
        return 0
    status = os.path.join(inp.get("cwd") or os.getcwd(), "docs", "STATUS.md")
    if os.path.exists(status):
        with open(status, encoding="utf-8", errors="replace") as fh:
            text = "docs/STATUS.md (already loaded, do not read again):\n\n" + fh.read()[:8000]
    else:
        text = "This project has no docs/STATUS.md; the first hand-off creates it."
    return emit({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": RULES + "\n\n" + text}})


def on_prompt(inp):
    sid = inp.get("session_id")
    context, last, ids, pending = read_transcript(inp.get("transcript_path"))
    state = load_state(sid, ids)
    if state.get("locked"):
        save_state(sid, state, ids)
        return emit({"decision": "block", "reason": "Session Guard: session closed (%s). Open a new session." % state.get("reason", "")})

    prompt = normalize(inp.get("prompt") or "")
    gap = time.time() - last if last else None
    if has_command(LONG, prompt):
        state["long"] = True
    long_mode = state.get("long")
    finish = has_command(FINISH, prompt)
    line = "[Session Guard] Context: %s (last reply). Mode: %s. Since last reply: %s.%s" % (
        k(context), "LONG" if long_mode else "normal", "unknown" if gap is None else "%d min" % (gap // 60),
        " Background jobs still running: %d (break rule off)." % pending if pending else "")
    notes, user = [], []

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

    if not state.get("locked"):
        step = step_for(context, long_mode)
        if step:
            if step[1] not in NOTIFY_ONCE or step[1] not in state["notified"]:
                notes.append(step[2])
            if step[1] not in state["notified"]:
                state["notified"].append(step[1])
                user.append(step[3])

    save_state(sid, state, ids)
    data = {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": "\n".join([line] + notes)}}
    if user:
        data["systemMessage"] = "Session Guard: " + " ".join(user)
    return emit(data)


def on_tool(inp):
    """While Claude works: tells once when a new threshold is passed; stops the turn at the hard limit."""
    sid = inp.get("session_id")
    context, _, ids, _ = read_transcript(inp.get("transcript_path"))
    state = load_state(sid, ids)
    if state.get("locked"):
        return 0
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


def plugin_installed():
    """True when the plugin copy exists under ~/.claude/plugins (then the project copy stays quiet)."""
    pattern = os.path.join(os.path.expanduser("~"), ".claude", "plugins", "**", "session_guard.py")
    return any(os.path.abspath(p) != SELF for p in glob.glob(pattern, recursive=True))


def main():
    args = sys.argv[1:]
    if args[:1] == ["--report"]:
        return report(args[1] if len(args) > 1 else None)
    try:
        if "--project" in args and plugin_installed():
            return 0
        inp = json.loads(sys.stdin.buffer.read().decode("utf-8", errors="replace") or "{}")
        event = inp.get("hook_event_name")
        if event == "SessionStart":
            return on_session_start(inp)
        if event == "UserPromptSubmit":
            return on_prompt(inp)
        if event == "PostToolUse":
            return on_tool(inp)
    except Exception:  # the guard must never break the user's work
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
