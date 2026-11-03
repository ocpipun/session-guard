# SESSION-GUARD: 2.0 | component: sg2-measure | version: 2.0.0
"""Context, break and quota measurement. Carried over from v1.4, where it was tested in real use; only the
module boundaries are new.

UNVERIFIED (official docs): the transcript entry schema is not documented and may change between Claude Code
versions. Only the file location is documented. Every read here tolerates missing fields.
"""
import json
import os
import re
import time
from datetime import datetime

from . import core

K = core.K
NORMAL_LIMIT, LONG_LIMIT, BREAK_CONTEXT, BREAK_SECONDS = 400 * K, 900 * K, 100 * K, 3600
TAIL_BYTES = 4 * 1024 * 1024
PENDING_MAX = 24 * 3600

# (threshold, key, instruction for Claude, hard stop). From v1.4; a hand-off is per task, and in long mode the
# hand-off is written at 800k, where work stops (1.4: prepared at 700k while work went on, so it went stale).
NORMAL_STEPS = [
    (400 * K, "n400", "HARD LIMIT (400k). Do no work. Tell the user the session is closed and to open a new one.", True),
    (350 * K, "n350", "TARGET STOP (350k). Start no new task. Finish the hand-off of every task you own if not done "
                      "(skill session-guard). Tell the user to open a new session.", False),
    (300 * K, "n300", "HAND-OFF (300k). Hand off every task you own now (skill session-guard, \"Hand-off\"). From now on "
                      "start no new task; only answer information questions and update hand-off notes on request.", False),
    (250 * K, "n250", "NOTICE (250k). Tell the user the context passed 250k.", False),
]
LONG_STEPS = [
    (900 * K, "l900", "HARD LIMIT (900k, long mode). Do no work. Tell the user the session is closed.", True),
    (800 * K, "l800", "HAND-OFF + NO WORK (800k, long mode). Do no new work. Hand off every task you own now (skill "
                      "session-guard, \"Hand-off\"). From now on only answer simple, specific information questions.", False),
    (700 * K, "l700", "NOTICE (700k, long mode). Tell the user. Keep the notes of every task you own current (sg task "
                      "note); write no hand-off yet, it is written at 800k. You may keep working.", False),
    (300 * K, "l300", "NOTICE (300k, long mode). Only tell the user; no hand-off.", False),
    (250 * K, "l250", "NOTICE (250k, long mode). Only tell the user; no hand-off.", False),
]
NOTIFY_ONCE = {"n250", "l250", "l300", "l700"}
HANDOFF_STEPS = {"n300", "n350", "l800"}
# Once this step was given, the hand-off is prepared: long mode can no longer be switched on and "finish session"
# is refused, so no second, stale hand-off of the same session is written.
HANDED_AT = {False: ("n300", "300k"), True: ("l800", "800k")}
NOTICE_FORM = (' Tell the user in one line, in their language, in this form: "%dk threshold passed: <what this means '
               'now, in a few words>. *This is a Session Guard notice." Then keep to the instruction; in the middle of '
               'work, give the line and go on working.')

FINISH = r"finish\s+session"
LONG = r"start\s+long\s+mode"
CONTINUE_INSTALL = r"continue\s+installation"


def normalize(text):
    # Commands are English; "FINISH" on a Turkish keyboard lowercases to "fınısh", so ı/İ count as i.
    return (text or "").replace("İ", "i").lower().replace("ı", "i")


def has_command(pattern, text):
    """At the start of the message (then punctuation or the end) or at its end; never inside a sentence."""
    return bool(re.search(r"^[\W_]*" + pattern + r"(?:[.,!;:\n]|\s*$)", text) or re.search(pattern + r"[\W_]*$", text))


def step_for(context, long_mode):
    for step in (LONG_STEPS if long_mode else NORMAL_STEPS):
        if context is not None and context >= step[0]:
            return step
    return None


def handed(rec):
    """The threshold (e.g. "300k") whose hand-off this session was already told to prepare, or None."""
    key, k = HANDED_AT[bool(rec.get("long"))]
    return k if key in rec.get("notified", []) else None


def kfmt(n):
    return "unknown" if n is None else "%dk" % round(n / K)


def is_prompt(entry, content):
    if entry.get("type") != "user" or entry.get("isMeta"):
        return False
    raw = entry["message"].get("content")
    if isinstance(raw, str):
        return not raw.lstrip().startswith("<")
    return any(isinstance(p, dict) and p.get("type") == "text" and not (p.get("text") or "").lstrip().startswith("<")
               for p in content) and not any(isinstance(p, dict) and p.get("type") == "tool_result" for p in content)


class Reading:
    __slots__ = ("context", "last_reply", "ids", "pending", "stale", "prompt_at", "turn_open", "first_at", "peak",
                 "output")

    def __init__(self):
        self.context = self.last_reply = self.prompt_at = self.first_at = None
        self.ids, self.pending, self.stale, self.turn_open, self.peak, self.output = [], 0, 0, False, 0, 0


def read_transcript(path):
    """Context (input tokens of the last main reply), last reply time, message ids, open background jobs, last
    prompt time, whether the turn is open, the first entry's time (how old the conversation is), peak context and
    output tokens (migration budget)."""
    r = Reading()
    if not path or not os.path.exists(path):
        return r
    size = os.path.getsize(path)
    with open(path, "rb") as fh:
        head = fh.readline().decode("utf-8", errors="replace")
        fh.seek(max(0, size - TAIL_BYTES))
        lines = fh.read().decode("utf-8", errors="replace").splitlines()
    for line in [head] + lines[:50]:
        try:
            t = core.to_time(json.loads(line).get("timestamp"))
        except (ValueError, AttributeError):
            continue
        if t:
            r.first_at = t
            break
    last_kind = None
    launched, agent_of, done, seen_out = {}, {}, set(), {}
    for line in lines:
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
            if msg.get("id") and msg.get("id") not in r.ids:
                r.ids.append(msg["id"])
            u = msg.get("usage")
            if isinstance(u, dict):
                r.context = (u.get("input_tokens") or 0) + (u.get("cache_creation_input_tokens") or 0) + \
                            (u.get("cache_read_input_tokens") or 0)
                r.peak = max(r.peak, r.context)
                if msg.get("id"):
                    seen_out[msg["id"]] = u.get("output_tokens") or 0
                r.last_reply = core.to_time(entry.get("timestamp")) or r.last_reply
            for part in content:
                if isinstance(part, dict) and part.get("type") == "tool_use":
                    args = part.get("input") or {}
                    if args.get("run_in_background") is True or part.get("name") == "Agent":
                        launched[part.get("id")] = core.to_time(entry.get("timestamp")) or time.time()
        elif entry.get("type") == "user":
            if is_prompt(entry, content):
                r.prompt_at = core.to_time(entry.get("timestamp")) or r.prompt_at
                last_kind = "prompt"
            elif any(isinstance(p, dict) and p.get("type") == "tool_result" for p in content):
                last_kind = "result"
            for part in content:
                if isinstance(part, dict) and part.get("type") == "tool_result" and part.get("tool_use_id") in launched:
                    found = re.search(r"agentId: (\w+)", json.dumps(part.get("content")))
                    if found:
                        agent_of[part["tool_use_id"]] = found.group(1)
    t = time.time()
    open_jobs = [at for tid, at in launched.items() if tid not in done and agent_of.get(tid) not in done]
    r.pending = sum(1 for at in open_jobs if t - at < PENDING_MAX)
    r.stale = len(open_jobs) - r.pending
    r.turn_open = last_kind in ("tool", "result", "prompt")
    r.output = sum(seen_out.values())
    return r


def break_decision(reading, long_mode, finishing):
    """None, ("remind", gap) or ("lock", gap). The v1.4 break rule: normal mode, no own background job, gap over
    one hour; over 100k context the message is not run."""
    if long_mode or finishing or reading.pending or reading.last_reply is None:
        return None
    gap = time.time() - reading.last_reply
    if gap <= BREAK_SECONDS:
        return None
    return ("lock" if reading.context is not None and reading.context > BREAK_CONTEXT else "remind"), gap


# --- Session Follow (quota), from v1.4 -------------------------------------------------
WINDOWS = (("five_hour", "5-hour"), ("weekly", "weekly"))
ASK_AT = {"five_hour": 85, "weekly": 95}
STOP_AT = {"five_hour": 90, "weekly": 98}
READING_MAX_AGE = 20 * 60
ASK_STEP = 5 * 60
RESET_MARGIN = 45 * 60
QUOTA_GRACE = 15 * 60
USAGE_TOOL = "mcp__ccd_session_mgmt__get_usage"
READ_USAGE = ("[Session Guard] QUOTA READING: call the tool %s once now (no arguments), in parallel with your next "
              "tool call; if it is not loaded, load it with ToolSearch \"select:%s\". Session Guard reads the result "
              "itself; do not mention it to the user." % (USAGE_TOOL, USAGE_TOOL))
QUOTA_STOP = ("QUOTA STOP (%s quota %d%%, resets at %s). Start no new task, the user's message included if it is one. "
              "Bring the current work to a hand-over-ready state within 15 minutes: record where each task you own "
              "stopped with `sg task note <TASK-ID> --pending \"Stopped at: ...\"`; no hand-off note. Tell the user in "
              "one line. After 15 minutes the turn is stopped; prompts are blocked until the reset.")


def follow_dir():
    return os.path.join(core.state_dir(), "follow")


def follow_path(name):
    return os.path.join(follow_dir(), name + ".json")


def readings():
    found = {}
    for name, _ in WINDOWS:
        data = core.read_json(follow_path(name), {})
        if not isinstance(data, dict) or not data:
            continue
        if not isinstance(data.get("resets_at"), (int, float)) or data["resets_at"] <= time.time():
            core.write_json(follow_path(name), {})
            continue
        found[name] = data
    return found


def save_reading(name, percent, resets, source):
    if isinstance(resets, str):
        resets = core.to_time(resets)
    if not isinstance(percent, (int, float)) or not isinstance(resets, (int, float)):
        return
    if resets > 1e11:
        resets /= 1000.0
    core.write_json(follow_path(name), {"used_percent": float(percent), "resets_at": float(resets),
                                        "read_at": time.time(), "source": source})


def usage_windows(obj, depth=0):
    if depth > 6:
        return None
    if isinstance(obj, str):
        try:
            return usage_windows(json.loads(obj), depth + 1)
        except ValueError:
            return None
    if isinstance(obj, dict):
        if isinstance(obj.get("windows"), list):
            return obj["windows"]
        obj = list(obj.values())
    if isinstance(obj, list):
        for item in obj:
            found = usage_windows(item, depth + 1)
            if found is not None:
                return found
    return None


def take_usage(response):
    for win in usage_windows(response) or []:
        if not isinstance(win, dict):
            continue
        label = str(win.get("label") or "").lower()
        name = "five_hour" if label.startswith("5-hour") else "weekly" if "weekly" in label and "all models" in label else None
        if name:
            save_reading(name, win.get("percentUsed"), win.get("resetsAt"), "desktop")


def quota_hit():
    for name, reading in sorted(readings().items()):
        if reading.get("used_percent", 0) >= STOP_AT[name] and reading["resets_at"] - time.time() >= RESET_MARGIN:
            return name, reading
    return None


def quota_cleared(info):
    if time.time() >= info.get("resets_at", 0):
        return True
    reading = readings().get(info.get("window"))
    return bool(reading and reading.get("read_at", 0) > info.get("at", 0) and
                reading.get("used_percent", 0) < STOP_AT.get(info.get("window"), 100))


def clock(epoch):
    return datetime.fromtimestamp(epoch).strftime("%H:%M" if epoch - time.time() < 20 * 3600 else "%a %H:%M")


def needs_reading(state, during_work):
    """Desktop app only. UNVERIFIED: CLAUDE_CODE_ENTRYPOINT is not in the official docs (seen in Bash, v1.4)."""
    if core.in_cloud() or os.environ.get("CLAUDE_CODE_ENTRYPOINT") != "claude-desktop":
        return False
    found, t = readings(), time.time()
    high = any(r.get("used_percent", 0) >= ASK_AT[n] for n, r in found.items())
    newest = max([r.get("read_at", 0) for r in found.values()] or [0])
    if not high and t - newest < READING_MAX_AGE:
        return False
    if during_work and t - state.get("asked_at", 0) < ASK_STEP:
        return False
    state["asked_at"] = t
    return True
