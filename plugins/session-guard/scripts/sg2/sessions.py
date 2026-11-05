# SESSION-GUARD: 2.0 | component: sg2-sessions | version: 2.0.0
"""Machine-side session records: ~/.claude/session_guard_v2/sessions/<claude session id>.json.

Each Claude Code session (its documented session_id) gets one Session Guard SESSION-ID (S-YYYYMMDD-xxxxxx) at
SessionStart. A reopened conversation keeps its record: the same session_id on --resume (documented), or, when
the app reopens it under a new id, the shared assistant message ids (v1.4 mechanism, kept).
Lock types (each stored on its own; clearing one never clears another):
    context, finish, break   permanent (v1.4)
    quota                    until the window resets (v1.4)
    handed                   every task of the session was taken over by another session (2.0)
    legacy                   the conversation began before its project moved to 2.0 (2.0 migration)
    home                     the project lives in the other place (computer / cloud)
A session with no record at all (it started before this install or update) is blocked as "restart" (v1.4).
"""
import glob
import os
import time

from . import core

KEEP_IDS = 10
MAX_AGE = 30 * 86400
PERMANENT = ("context", "finish", "break", "handed", "legacy", "home")


def folder():
    return os.path.join(core.state_dir(), "sessions")


def path_for(claude_id):
    return os.path.join(folder(), core.safe_name(claude_id) + ".json")


def blank(claude_id):
    return {"signature": "SESSION-GUARD: 2.0", "claude_id": claude_id, "sg_id": None, "self": None, "root": None, "project_id": None,
            "born": None, "long": False, "locks": {}, "notified": [], "ids": [], "asked_at": 0, "stop_blocks": 0,
            "break_probe": None, "place": core.place()}


def fix(rec, claude_id):
    base = blank(claude_id)
    if isinstance(rec, dict):
        base.update({k: v for k, v in rec.items() if k in base})
    if not isinstance(base["locks"], dict):
        base["locks"] = {}
    base["claude_id"] = claude_id
    return base


def load(claude_id, ids=()):
    rec = core.read_json(path_for(claude_id))
    if isinstance(rec, dict):
        return fix(rec, claude_id), True
    if ids:
        known = set(ids)
        for p in glob.glob(os.path.join(folder(), "*.json")):
            old = core.read_json(p)
            if isinstance(old, dict) and known & set(old.get("ids") or []):
                rec = fix(old, claude_id)
                rec["continues"] = old.get("claude_id")  # not saved: fix() keeps only known fields
                return rec, True
    return blank(claude_id), False


def save(rec, ids=()):
    if ids:
        rec["ids"] = list(ids)[-KEEP_IDS:]
    core.write_json(path_for(rec["claude_id"]), rec)


def lock(rec, kind, reason, **extra):
    rec["locks"][kind] = dict(extra, reason=reason, at=time.time())


def closed(rec):
    for kind in PERMANENT:
        if kind in rec["locks"]:
            return kind
    return None


def clean():
    """Drops records unused for 30 days, except locked ones (a reopened old session stays locked)."""
    for p in glob.glob(os.path.join(folder(), "*.json")):
        try:
            if time.time() - os.path.getmtime(p) <= MAX_AGE:
                continue
            rec = core.read_json(p, {})
            if not (isinstance(rec, dict) and any(k in (rec.get("locks") or {}) for k in PERMANENT)):
                os.remove(p)
        except OSError:
            pass


def v1_lock(claude_id):
    """A lock v1.4 set on this session (read only, never written): 2.0 keeps it."""
    rec = core.read_json(os.path.join(core.v1_state_dir(), core.safe_name(claude_id) + ".json"))
    if not isinstance(rec, dict):
        return None
    locks = rec.get("locks") if isinstance(rec.get("locks"), dict) else {}
    for kind in ("context", "finish", "break"):
        if kind in locks:
            return kind
    if rec.get("locked"):
        return "finish"
    return None
