# SESSION-GUARD: 2.0 | component: sg2-store | version: 2.0.0
"""The project store: PROJECT-ID, sessions, tasks, ideas and hand-offs of one project, in
<repo>/.claude/session-guard2/store.json.

Concurrency: every change runs inside mutate(): one cross-process lock, a fresh read, the change, validation, a
revision bump and an atomic write. A caller that decided something from an older read passes expect_rev and is
refused when the store moved on. Ownership is checked on every task change, so a session can never overwrite a
task another session owns. docs/STATUS.md and the other project files are rendered from the store in the same
lock: nobody edits the task part of STATUS by hand, so two sessions cannot overwrite each other's lines.
"""
import os

from . import SG_VERSION, core

SCHEMA = 1
ACTIVE = ("open", "in_progress", "blocked")
CLOSED = ("done", "cancelled")
KINDS = ("normal", "urgent", "setup", "migration")
EXEMPT = ("setup", "migration")
PRIORITIES = ("P1", "P2", "P3")
MAX_NORMAL, MAX_URGENT, MAX_MAIN = 3, 1, 4


class Refused(Exception):
    """A change the rules do not allow; the message tells Claude what to do instead."""


def path(root):
    return os.path.join(core.project_dir(root), "store.json")


def lock_path(root):
    return os.path.join(core.project_dir(root), "store.lock")


def load(root):
    data = core.read_json(path(root))
    return data if isinstance(data, dict) and data.get("project_id") else None


def is_project(root):
    return bool(root) and load(root) is not None


def blank(name, home):
    return {"schema": SCHEMA, "session_guard": SG_VERSION, "project_id": core.new_project_id(), "name": name,
            "home": home, "created": core.iso(), "migrated_at": None, "migration_sessions": [], "rev": 0,
            "next_task": 1, "next_idea": 1, "tasks": {}, "sessions": {}, "ideas": {}, "legacy_sessions": [],
            "events": []}


def init(root, name=None, home=None, migrated=False, migration_session=None):
    """Creates the store once (O_EXCL under the lock); an existing store is returned unchanged."""
    with core.FileLock(lock_path(root)):
        data = load(root)
        if data:
            return data, False
        data = blank(name or os.path.basename(root), home or core.place())
        if migrated:
            data["migrated_at"] = core.now()
        if migration_session:
            data["migration_sessions"].append(migration_session)
        os.makedirs(core.project_dir(root), exist_ok=True)
        gi = os.path.join(core.project_dir(root), ".gitignore")
        if not os.path.exists(gi):
            core.write_text(gi, "# SESSION-GUARD: 2.0 | component: project-gitignore | version: 2.0.0\nstore.lock\n*.tmp\n"
                                "__pycache__/\n*.pyc\n")
        core.write_json(path(root), data)
        return data, True


def mutate(root, change, expect_rev=None, render=True):
    """Runs change(store) -> result under the lock. Refused leaves the store untouched."""
    from . import render as rnd
    with core.FileLock(lock_path(root)):
        data = load(root)
        if data is None:
            raise Refused("no Session Guard 2.0 project here (not migrated): %s" % root)
        if expect_rev is not None and int(expect_rev) != data["rev"]:
            raise Refused("the store changed since you read it (rev %s, now %d): read it again (`sg task list`) and "
                          "redo the change" % (expect_rev, data["rev"]))
        data["events"] = []
        result = change(data)
        validate(data)
        data["rev"] += 1
        events = data.pop("events")
        core.write_json(path(root), dict(data, events=[]))
        if render:
            rnd.after_change(root, data, events)
        return result


# --- validation ------------------------------------------------------------------------
def main_counts(data):
    normal = urgent = 0
    for t in data["tasks"].values():
        if t["parent"] is None and t["status"] in ACTIVE:
            if t["kind"] == "normal":
                normal += 1
            elif t["kind"] == "urgent":
                urgent += 1
    return normal, urgent


def validate(data):
    normal, urgent = main_counts(data)
    if normal > MAX_NORMAL or urgent > MAX_URGENT or normal + urgent > MAX_MAIN:
        raise Refused("active main task limit: at most %d normal + %d urgent (now %d + %d)" % (
            MAX_NORMAL, MAX_URGENT, normal, urgent))
    for tid, t in data["tasks"].items():
        if not core.TASK_ID.match(tid) or t["id"] != tid:
            raise Refused("bad task id " + tid)
        if t["parent"] and t["parent"] not in data["tasks"]:
            raise Refused("task %s has a missing parent" % tid)


# --- sessions -----------------------------------------------------------------------------
def session_rec(data, sid):
    return data["sessions"].get(sid)


def register_session(data, sid, claude_id, place, parent=None):
    rec = data["sessions"].get(sid)
    if rec is None:
        rec = data["sessions"][sid] = {"id": sid, "claude_ids": [], "place": place, "started": core.iso(),
                                       "parent": None, "children": [], "continues": [], "state": "active",
                                       "lock_reason": None, "ended": None}
    if claude_id and claude_id not in rec["claude_ids"]:
        rec["claude_ids"].append(claude_id)
    if parent and parent != sid:
        link(data, parent, sid)
    return rec


def link(data, parent, child):
    p, c = data["sessions"].get(parent), data["sessions"].get(child)
    if not c:
        return
    if c["parent"] is None and parent != child:
        c["parent"] = parent
        if p and child not in p["children"]:
            p["children"].append(child)
    elif parent not in c["continues"] and parent != c["parent"]:
        c["continues"].append(parent)


def lock_session(data, sid, reason):
    rec = data["sessions"].get(sid)
    if rec and rec["state"] != "locked":
        rec["state"], rec["lock_reason"] = "locked", reason
        data["events"].append(("session_locked", sid, reason))


def owned_open(data, sid):
    return [t for t in data["tasks"].values() if t["owner"] == sid and t["status"] in ACTIVE]


# --- tasks -----------------------------------------------------------------------------------
def task(data, tid):
    t = data["tasks"].get(tid)
    if not t:
        raise Refused("no task %s in project %s" % (tid, data["project_id"]))
    return t


def children(data, tid):
    return sorted((t for t in data["tasks"].values() if t["parent"] == tid), key=lambda t: sort_key(t["id"]))


def sort_key(tid):
    return [int(p) for p in tid.split("-")[1:]]


def _new_id(data, parent):
    if parent is None:
        tid = "TASK-%03d" % data["next_task"]
        data["next_task"] += 1
        return tid
    n = 1 + max([sort_key(t["id"])[-1] for t in data["tasks"].values() if t["parent"] == parent] + [0])
    tid = "%s-%d" % (parent, n)
    while tid in data["tasks"]:  # never reuse an id, even of a deleted record
        n += 1
        tid = "%s-%d" % (parent, n)
    return tid


def _check_owner(data, sid, t):
    if t["owner"] != sid:
        rec = data["sessions"].get(t["owner"]) if t["owner"] else None
        raise Refused("%s is owned by %s (%s): only its owner changes it. Claim it first (`sg task claim %s`)" % (
            t["id"], t["owner"] or "nobody", rec["state"] if rec else "no record", t["id"]))
    rec = data["sessions"].get(sid)
    if rec and rec["state"] == "locked":
        raise Refused("session %s is locked (%s): it changes nothing any more" % (sid, rec["lock_reason"]))


def new_task(data, sid, title, kind="normal", priority="P2", parent=None, approval=None, reason=None, related=()):
    title = (title or "").strip()
    if not title:
        raise Refused("a task needs a title")
    if kind not in KINDS:
        raise Refused("kind must be one of " + ", ".join(KINDS))
    if priority not in PRIORITIES:
        raise Refused("priority must be one of " + ", ".join(PRIORITIES))
    if parent:
        p = task(data, parent)
        if p["status"] in CLOSED:
            raise Refused("%s is %s: a closed task gets no new subtask; open a new task related to it "
                          "(`sg task reopen %s`)" % (parent, p["status"], parent))
        kind = p["kind"] if p["kind"] in EXEMPT else "normal"
    else:
        if kind not in EXEMPT and not (approval or "").strip():
            raise Refused("a new active main task needs the user's approval: ask the user, then pass "
                          "--approval \"<the user's words>\". Unrelated work goes to the backlog (`sg idea add`)")
        normal, urgent = main_counts(data)
        if kind == "normal" and normal >= MAX_NORMAL:
            raise Refused("3 normal main tasks are active. Finish one, put the work in the backlog (`sg idea add`), "
                          "or, if it cannot wait, ask the user for one urgent task (--kind urgent --reason ...)")
        if kind == "urgent":
            if normal < MAX_NORMAL:
                raise Refused("an urgent task is only for when 3 normal tasks are active; create a normal task "
                              "with --priority P1")
            if not (reason or "").strip():
                raise Refused("an urgent task needs --reason")
            if urgent >= MAX_URGENT:
                raise Refused("one urgent task is already active")
    tid = _new_id(data, parent)
    t = data["tasks"][tid] = {
        "id": tid, "title": title, "kind": kind, "priority": priority, "status": "open", "parent": parent,
        "owner": sid, "owners": [{"session": sid, "from": core.iso(), "to": None, "how": "created"}],
        "done": [], "pending": [], "notes": [], "related": [], "approval": approval, "reason": reason,
        "created": core.iso(), "updated": core.iso(), "completed": None, "handoff": None, "flags": []}
    for rel_id, relation in related:
        task(data, rel_id)
        t["related"].append({"task": rel_id, "relation": relation})
        back = {"task": tid, "relation": "reopened-by" if relation == "reopens" else "extended-by"
                if relation == "extends" else "related"}
        if back not in data["tasks"][rel_id]["related"]:
            data["tasks"][rel_id]["related"].append(back)
    data["events"].append(("task_new", tid))
    return t


def _transfer(data, t, new_owner, how):
    for o in t["owners"]:
        if o["to"] is None:
            o["to"] = core.iso()
    t["owners"].append({"session": new_owner, "from": core.iso(), "to": None, "how": how})
    t["owner"] = new_owner
    t["updated"] = core.iso()


def claim(data, sid, tid, takeover_approval=None):
    """Takes over a task. Free: no owner, the owner's session ended or locked, or the task carries a hand-off.
    An active owner keeps it unless the user approved a takeover. The old owner's history is kept."""
    t = task(data, tid)
    if t["status"] in CLOSED:
        raise Refused("%s is %s. Redoing or extending it is a new task: `sg task reopen %s`" % (tid, t["status"], tid))
    me = data["sessions"].get(sid)
    if me and me["state"] == "locked":
        raise Refused("session %s is locked" % sid)
    if t["owner"] == sid:
        return "already yours"
    old = t["owner"]
    old_rec = data["sessions"].get(old) if old else None
    if t["handoff"]:
        how = "hand-off from %s" % t["handoff"]["source_session"]
    elif old is None:
        how = "claimed (no owner)"
    elif old_rec is None or old_rec["state"] in ("ended", "locked"):
        how = "claimed (owner %s %s)" % (old, old_rec["state"] if old_rec else "unknown")
    elif (takeover_approval or "").strip():
        how = "takeover approved by user: %s" % takeover_approval.strip()
    else:
        raise Refused("%s is owned by %s, which is still active. Do not take it; ask the user. With the user's "
                      "yes: `sg task claim %s --takeover \"<the user's words>\"`" % (tid, old, tid))
    _transfer(data, t, sid, how)
    if t["handoff"]:
        h = dict(t["handoff"], claimed_by=sid, claimed_at=core.iso())
        data["events"].append(("handoff_claimed", tid, h))
        if old:
            link(data, old, sid)
        t["handoff"] = None
    if "orphaned" in t["flags"]:
        t["flags"].remove("orphaned")
    if old and old != sid and old_rec and not owned_open(data, old):
        lock_session(data, old, "all tasks handed over (last: %s to %s)" % (tid, sid))
    return how


def note(data, sid, tid, done=(), pending=(), resolved=(), status=None, priority=None, text=None):
    t = task(data, tid)
    _check_owner(data, sid, t)
    if t["status"] in CLOSED:
        raise Refused("%s is %s" % (tid, t["status"]))
    before = (list(t["done"]), list(t["pending"]), t["status"], t["priority"], len(t["notes"]))
    t["done"] += [d.strip() for d in done if d.strip()]
    for item in resolved:
        if item in t["pending"]:
            t["pending"].remove(item)
            t["done"].append(item)
    t["pending"] += [p.strip() for p in pending if p.strip() and p.strip() not in t["pending"]]
    if status:
        if status not in ACTIVE:
            raise Refused("status must be one of %s (use `sg task done` / `sg task cancel`)" % ", ".join(ACTIVE))
        t["status"] = status
    if priority:
        if priority not in PRIORITIES:
            raise Refused("priority must be one of " + ", ".join(PRIORITIES))
        t["priority"] = priority
    if text:
        t["notes"].append("%s %s: %s" % (core.today(), sid, text.strip()))
    changed = before != (t["done"], t["pending"], t["status"], t["priority"], len(t["notes"]))
    if changed and t["handoff"] and t["handoff"]["source_session"] == sid:
        t["handoff"]["stale"] = True  # the work moved on after the note was written
    t["updated"] = core.iso()
    return t


def close(data, sid, tid, status="done", summary=None, force=False):
    """A subtask closing never closes its parent; a parent with open subtasks closes only with force."""
    t = task(data, tid)
    _check_owner(data, sid, t)
    if t["status"] in CLOSED:
        return "already " + t["status"]
    open_kids = [c["id"] for c in children(data, tid) if c["status"] in ACTIVE]
    if open_kids and not force:
        raise Refused("%s has open subtasks (%s): close them first, or pass --force after asking the user" % (
            tid, ", ".join(open_kids)))
    t["status"], t["completed"], t["updated"] = status, core.iso(), core.iso()
    if summary:
        t["done"].append(summary.strip())
    for o in t["owners"]:
        if o["to"] is None:
            o["to"] = core.iso()
    t["handoff"] = None
    data["events"].append(("task_closed", tid))
    return t


def reopen(data, sid, tid, title, relation="reopens", approval=None, priority="P2"):
    """A wrongly finished task done again, or a feature added to a finished one, is a new task with a new id."""
    old = task(data, tid)
    if old["status"] not in CLOSED:
        raise Refused("%s is still active: continue it under the same id" % tid)
    if relation not in ("reopens", "extends"):
        raise Refused("relation must be reopens or extends")
    parent = old["parent"] if old["parent"] and data["tasks"][old["parent"]]["status"] in ACTIVE else None
    return new_task(data, sid, title, kind="normal" if old["kind"] == "urgent" else old["kind"], priority=priority,
                    parent=parent, approval=approval, related=[(tid, relation)])


def relate(data, sid, tid, other, relation="related"):
    t, o = task(data, tid), task(data, other)
    _check_owner(data, sid, t)
    if {"task": other, "relation": relation} not in t["related"]:
        t["related"].append({"task": other, "relation": relation})
    if {"task": tid, "relation": relation} not in o["related"]:
        o["related"].append({"task": tid, "relation": relation})


# --- hand-offs ------------------------------------------------------------------------------------
HANDOFF_FIELDS = ("project_id", "task_id", "source_session", "session_parent", "session_children", "last_developer",
                  "status", "done", "pending", "owner", "branch", "pr", "technical", "user_command", "work", "created",
                  "state", "push")


def set_work(data, sid, tid, work):
    """Where the task's unfinished work is: {"mode": "folder" | "side-branch", ...} (cli `handoff write`)."""
    t = task(data, tid)
    _check_owner(data, sid, t)
    t["work"] = work
    return t


def handoff(data, sid, tid, technical, done=(), pending=(), developer=None, branch=None, pr=None, user_command=None):
    """Writes (or refreshes) the task's hand-off. Not repeated when nothing changed; never for a closed task."""
    t = task(data, tid)
    _check_owner(data, sid, t)
    if t["status"] in CLOSED:
        raise Refused("%s is %s: a finished task needs no hand-off" % (tid, t["status"]))
    if not (technical or "").strip():
        raise Refused("a hand-off needs --technical: what the next session must know to continue (files, commands, "
                      "where it stopped)")
    if done or pending:
        note(data, sid, tid, done=done, pending=pending)
    rec = data["sessions"].get(sid) or {}
    old = t["handoff"] if t["handoff"] and t["handoff"]["source_session"] == sid else None
    h = {"project_id": data["project_id"], "task_id": tid, "source_session": sid,
         "session_parent": rec.get("parent"), "session_children": list(rec.get("children") or []),
         "last_developer": developer or (old or {}).get("last_developer") or sid, "status": t["status"],
         "done": list(t["done"]), "pending": list(t["pending"]), "owner": t["owner"], "branch": branch, "pr": pr,
         "technical": technical.strip(),
         "user_command": user_command if user_command is not None else
         (old or {}).get("user_command") or t.get("pending_user_command"), "work": t.get("work"),
         "created": core.iso(), "state": "prepared", "push": None}
    if old and not old.get("stale") and all(old.get(f) == h[f] for f in HANDOFF_FIELDS
                                           if f not in ("created", "state", "push", "work")) \
            and _same_work(old.get("work"), h["work"]):
        return "unchanged"
    t["handoff"] = h
    t["updated"] = core.iso()
    data["events"].append(("handoff_prepared", tid))
    return "prepared"


def _same_work(a, b):
    skip = ("saved", "pushed")
    return {k: v for k, v in (a or {}).items() if k not in skip} == {k: v for k, v in (b or {}).items() if k not in skip}


def record_user_command(data, sid, command):
    """Break rule: the hook itself stores the user's last message word for word in each open task's hand-off."""
    for t in owned_open(data, sid):
        if t["handoff"] and t["handoff"]["source_session"] == sid:
            t["handoff"]["user_command"] = command
        else:
            t["pending_user_command"] = command


def mark_published(data, sid, pushes):
    """pushes: {task id: push result}; a task missing from it keeps its work in the folder (state "local")."""
    n = 0
    for t in data["tasks"].values():
        h = t["handoff"]
        if h and h["source_session"] == sid and h["state"] == "prepared":
            push = pushes.get(t["id"])
            h["state"] = "published" if push and push.get("verified") else "local"
            h["push"] = push
            if push and push.get("verified") and t.get("work"):
                t["work"]["pushed"] = push["commit"]
                h["work"] = dict(t["work"])
            n += 1
    return n


def missing_handoffs(data, sid):
    return [t["id"] for t in owned_open(data, sid) if not (t["handoff"] and t["handoff"]["source_session"] == sid)]


def waiting_handoffs(data, sid=None):
    return [t for t in data["tasks"].values() if t["handoff"] and t["status"] in ACTIVE and t["owner"] != sid
            and t["handoff"]["state"] in ("published", "local", "prepared")]


def claimable(data, sid):
    """Tasks a new session could take: hand-offs waiting, or orphaned (owner ended without one)."""
    out = waiting_handoffs(data, sid)
    for t in data["tasks"].values():
        rec = data["sessions"].get(t["owner"]) if t["owner"] else None
        if t not in out and t["status"] in ACTIVE and t["owner"] != sid and (
                t["owner"] is None or rec is None or rec["state"] in ("ended", "locked")):
            out.append(t)
    return sorted(out, key=lambda t: sort_key(t["id"]))


def end_session(data, sid, reason):
    rec = data["sessions"].get(sid)
    if not rec or rec["state"] == "locked":
        return
    rec["state"], rec["ended"] = "ended", core.iso()
    for t in owned_open(data, sid):
        if not t["handoff"] and "orphaned" not in t["flags"]:
            t["flags"].append("orphaned")


# --- merging two copies of the store (git merge of a side branch) ------------------------------------
STATE_ORDER = {"active": 0, "ended": 1, "locked": 2}


def merge(ours, theirs):
    """The union of two stores of one project (a side branch merged into another branch). A task present in both
    keeps the copy changed last (only its owner changes a task); a session keeps its strongest state (a lock is
    never undone); counters never go back, so no id is reused."""
    if ours.get("project_id") != theirs.get("project_id"):
        raise Refused("different projects: %s / %s" % (ours.get("project_id"), theirs.get("project_id")))
    theirs = _renumber(ours, theirs)
    out = dict(ours)
    out["tasks"] = dict(theirs.get("tasks") or {})
    for tid, t in (ours.get("tasks") or {}).items():
        other = out["tasks"].get(tid)
        if other is None or (t.get("updated") or "") >= (other.get("updated") or ""):
            out["tasks"][tid] = t
    out["sessions"] = dict(theirs.get("sessions") or {})
    for sid, rec in (ours.get("sessions") or {}).items():
        other = out["sessions"].get(sid)
        if other is None:
            out["sessions"][sid] = rec
            continue
        keep = dict(rec if STATE_ORDER.get(rec["state"], 0) >= STATE_ORDER.get(other["state"], 0) else other)
        for f in ("claude_ids", "children", "continues"):
            keep[f] = list(dict.fromkeys((other.get(f) or []) + (rec.get(f) or [])))
        out["sessions"][sid] = keep
    out["ideas"] = dict(theirs.get("ideas") or {})
    for iid, idea in (ours.get("ideas") or {}).items():
        if iid not in out["ideas"] or idea["state"] != "waiting":
            out["ideas"][iid] = idea
    for f in ("next_task", "next_idea", "rev"):
        out[f] = max(ours.get(f) or 0, theirs.get(f) or 0)
    out["rev"] += 1
    for f in ("legacy_sessions", "migration_sessions"):
        out[f] = list(dict.fromkeys((theirs.get(f) or []) + (ours.get(f) or [])))
    out["events"] = []
    return out


def _same(a, b):
    """One task (or idea) seen on two branches, not two different ones that got the same id there."""
    first = lambda t: (t.get("owners") or [{}])[0].get("session")
    return a.get("created", a.get("added")) == b.get("created", b.get("added")) and first(a) == first(b) and \
        a.get("by") == b.get("by")


def _renumber(ours, theirs):
    """Sessions on two branches (two cloud checkouts) can give the same new id to different tasks. Their tasks keep
    their content and get the next free ids (subtasks follow their parent); every reference is rewritten."""
    import copy
    theirs = copy.deepcopy(theirs)
    ot, tt = ours.get("tasks") or {}, theirs.get("tasks") or {}
    nxt = max(ours.get("next_task") or 1, theirs.get("next_task") or 1)
    taken, mapping = set(ot) | set(tt), {}
    for tid in sorted(tt, key=sort_key):
        t = tt[tid]
        parent = mapping.get(t["parent"], t["parent"])
        clash = tid in ot and not _same(ot[tid], t)
        if not clash and parent == t["parent"]:
            continue
        if parent is None:
            new = "TASK-%03d" % nxt
            nxt += 1
        else:
            n = 1
            while "%s-%d" % (parent, n) in taken:
                n += 1
            new = "%s-%d" % (parent, n)
        taken.add(new)
        mapping[tid] = new
    if mapping:
        fix = lambda x: mapping.get(x, x)
        tasks = {}
        for tid, t in tt.items():
            t["id"], t["parent"] = fix(tid), fix(t["parent"])
            for r in t.get("related") or []:
                r["task"] = fix(r["task"])
            if t.get("handoff"):
                t["handoff"]["task_id"] = fix(t["handoff"]["task_id"])
            if tid in mapping:
                t.setdefault("notes", []).append("%s renumbered from %s (same id given on another branch)" % (
                    core.today(), tid))
            tasks[t["id"]] = t
        theirs["tasks"] = tasks
        for idea in (theirs.get("ideas") or {}).values():
            idea["task"] = fix(idea.get("task"))
    theirs["next_task"] = nxt
    oi, ti = ours.get("ideas") or {}, theirs.get("ideas") or {}
    nxi = max(ours.get("next_idea") or 1, theirs.get("next_idea") or 1)
    ideas = {}
    for iid in sorted(ti):
        idea = ti[iid]
        if iid in oi and not _same(oi[iid], idea):
            idea["id"] = "IDEA-%03d" % nxi
            nxi += 1
        ideas[idea["id"]] = idea
    theirs["ideas"], theirs["next_idea"] = ideas, nxi
    return theirs


# --- ideas (backlog) ----------------------------------------------------------------------------------
def add_idea(data, sid, text, source=None):
    iid = "IDEA-%03d" % data["next_idea"]
    data["next_idea"] += 1
    data["ideas"][iid] = {"id": iid, "text": text.strip(), "by": sid, "added": core.iso(), "source": source,
                          "state": "waiting", "task": None}
    return iid


def promote_idea(data, sid, iid, approval, priority="P2"):
    idea = data["ideas"].get(iid)
    if not idea or idea["state"] != "waiting":
        raise Refused("no waiting idea " + iid)
    t = new_task(data, sid, idea["text"], priority=priority, approval=approval)
    idea["state"], idea["task"] = "promoted", t["id"]
    return t
