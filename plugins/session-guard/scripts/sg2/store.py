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

import difflib
import re

from . import SG_VERSION, SIGNATURE, core

SCHEMA = 1
ACTIVE = ("open", "in_progress", "blocked")
QUEUED = "queued"
CLOSED = ("done", "cancelled")
KINDS = ("normal", "urgent", "setup", "migration", "rule-script")
EXEMPT = ("setup", "migration")
PRIORITIES = ("P1", "P2", "P3")
MAX_NORMAL, MAX_URGENT = 10, 3   # active main tasks per working agent (user decisions 2026-11-04): normal, urgent
MAX_QUEUE, MAX_IDEAS = 50, 50   # waiting main tasks, waiting ideas; subtasks and rule-script work not counted
LIMIT_TELL = ("Tell the user: Session Guard keeps at most 10 active main tasks (+3 urgent) per working agent, 50 "
              "queued tasks and 50 ideas. Finish or drop waiting work first.")


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
    return {"signature": SIGNATURE, "schema": SCHEMA, "session_guard": SG_VERSION, "project_id": core.new_project_id(),
            "name": name, "home": home, "created": core.iso(), "migrated_at": None, "migration_sessions": [],
            "rev": 0, "next_task": 1, "next_idea": 1, "next_rule": 1, "tasks": {}, "sessions": {}, "ideas": {},
            "rules": {}, "legacy_sessions": [], "events": []}


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
            if t["kind"] in ("normal", "rule-script"):
                normal += 1
            elif t["kind"] == "urgent":
                urgent += 1
    return normal, urgent


def agents(data, sid=None):
    """Agents working in the project now: active sessions owning an active main task, plus the asking session.
    Subagents are not agents: they work inside their session, on its subtasks."""
    out = {sid} if sid else set()
    for t in data["tasks"].values():
        rec = data["sessions"].get(t["owner"]) if t["owner"] else None
        if t["parent"] is None and t["status"] in ACTIVE and rec and rec["state"] == "active":
            out.add(t["owner"])
    return max(1, len(out))


def normal_limit(data, sid=None):
    """10 normal active main tasks for each agent working in the project at the same time (2 agents: 20)."""
    return MAX_NORMAL * agents(data, sid)


def urgent_limit(data, sid=None):
    """3 urgent active main tasks for each agent working in the project at the same time (2 agents: 6)."""
    return MAX_URGENT * agents(data, sid)


def urgent_open(data, owner=None):
    """Active urgent main tasks (of one owner when given)."""
    return sorted((t["id"] for t in data["tasks"].values() if t["parent"] is None and t["kind"] == "urgent"
                   and t["status"] in ACTIVE and (owner is None or t["owner"] == owner)), key=sort_key)


def _no_new_while_urgent(data, sid):
    """An urgent task belongs to its agent: only its owner takes no new task until it is finished (user decision
    2026-11-04); the other agents go on."""
    busy = urgent_open(data, sid)
    if busy:
        raise Refused("YOUR URGENT TASK IS ACTIVE (%s): you take no new task until it is finished. Tell the user; "
                      "record the request as an idea (`sg idea add`) so it is not lost" % ", ".join(busy))


def queue_count(data):
    return sum(1 for t in data["tasks"].values()
               if t["parent"] is None and t["status"] == QUEUED and t["kind"] != "rule-script")


def idea_count(data):
    return sum(1 for i in data["ideas"].values() if i["state"] == "waiting" and i.get("source") != "rule-review")


def _words(text):
    return " ".join(re.sub(r"[^\w ]+", " ", (text or "").lower()).split())


def similar(data, text, at_least=0.6, skip=()):
    """Existing tasks and ideas close to a new one: [(score, id, kind, status, title)], best first. A near copy
    (0.85+) is refused as a duplicate; the rest is shown so Claude checks it for conflicts or bugs."""
    me, out = _words(text), []
    items = [(t["id"], "task", t["status"], t["title"]) for t in data["tasks"].values()] + \
            [(i["id"], "idea", i["state"], i["text"]) for i in data["ideas"].values()]
    for iid, kind, state, title in items:
        if iid in skip:
            continue
        score = difflib.SequenceMatcher(None, me, _words(title)).ratio()
        if score >= at_least:
            out.append((round(score, 2), iid, kind, state, title))
    return sorted(out, reverse=True)[:5]


def related_text(found):
    if not found:
        return ""
    return ("Related existing work (check it for overlap, conflict or a bug the new work could cause; warn the "
            "user if so): " + "; ".join("%s %s (%s, %s) \"%s\"" % (i, k, s, sc, t[:60]) for sc, i, k, s, t in found))


STOP = set("with from that this into have will when then than they them what which also only each very been were "
           "için olarak gibi daha veya ile bir iki olan olsun şekilde kadar sonra önce bunu şunu".split())


def stems(text):
    """Keywords of a text, cut to 5 letters so Turkish and English word endings still match (görevler ~ görev)."""
    return {w[:5] for w in _words(text).split() if len(w) >= 4 and w not in STOP}


def word_hits(titles, items, keep=25):
    """items [(id, line)] sharing keywords with any of the titles, most shared first: how finished work, above all
    the pre-2.0 items without a 2.0 id, is found by search."""
    want = set().union(*[stems(t) for t in titles]) if titles else set()
    scored = [(len(want & stems(line)), iid, line) for iid, line in items]
    scored = sorted((s for s in scored if s[0]), key=lambda s: -s[0])
    return scored[:keep], max(0, len(scored) - keep)


def validate(data):
    """The per-agent limits are checked where a task is created or started, not here: a session ending lowers the
    limits and must never block the store."""
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


def resolve(data, sid):
    """A child session's first id stays an alias of its child id (so $SG_SESSION_ID keeps working)."""
    rec = (data or {}).get("sessions", {}).get(sid) or {}
    return rec.get("renamed_to") or sid


CHILD_KINDS = {"bound": "B", "independent": "I"}


def make_child(data, old, parent, kind):
    """A session started from another one gets its kind and the id <parent>-B<n> (bound) or <parent>-I<n>
    (independent) before it owns anything. Returns the new id, or None (unknown parent, already a child, or it
    already owns a task)."""
    p, me = data["sessions"].get(parent), data["sessions"].get(old)
    if kind not in CHILD_KINDS or not p or not me or me.get("parent") or me.get("renamed_to") or parent == old or \
            any(t["owner"] == old for t in data["tasks"].values()):
        return None
    n = 1
    while "%s-%s%d" % (parent, CHILD_KINDS[kind], n) in data["sessions"]:
        n += 1
    new = "%s-%s%d" % (parent, CHILD_KINDS[kind], n)
    data["sessions"][new] = dict(me, id=new, parent=None, children=[], continues=[], kind=kind)
    data["sessions"][old] = dict(me, state="renamed", renamed_to=new)
    link(data, parent, new)
    return new


def child_kind(data, sid):
    """How a session started from another one is used, fixed when it starts (user decision 2026-11-04):
    bound         works only on subtasks of its parent's work (a test, a part of it): it hands off and is locked
                  together with its parent
    independent   its own main tasks, never the parent's work: it goes on when the parent stops; it is only told
    continuation  took over the parent's hand-off: it works on as the parent did (no child kind)"""
    return (data["sessions"].get(sid) or {}).get("kind") or "continuation"


def _lineage(data, sid):
    """The sessions above sid: its parent, the parent's parent, ..."""
    out, rec = [], data["sessions"].get(sid) or {}
    while rec.get("parent") and rec["parent"] not in out and rec["parent"] != sid:
        out.append(rec["parent"])
        rec = data["sessions"].get(rec["parent"]) or {}
    return out


def _parents_work(data, sid, t):
    """The task belongs to the work of a session above sid: its main task is (or was) owned there."""
    while t["parent"]:
        t = data["tasks"][t["parent"]]
    above = _lineage(data, sid)
    return bool(above) and any(o["session"] in above for o in t["owners"])


def _not_handing_off(data, sid):
    """A session that hands off (300k / 800k hand-off, break, finish, hard stop, break watch) takes no new work and
    takes nothing back (user decision 2026-11-05): no new task or subtask, no start, no claim, no promotion."""
    rec = data["sessions"].get(sid) or {}
    if rec.get("stopped") or rec.get("state") == "locked":
        raise Refused("session %s is handing off (%s): it takes no new task and takes nothing back. Finish its "
                      "hand-offs; new work goes to a new session" % (sid, (rec.get("stopped") or {}).get("reason")
                                                                    or rec.get("lock_reason") or "locked"))


def _kind_allows(data, sid, t=None, parent=None):
    """The kinds never take each other's work: a bound child session agent works only on subtasks of its parent's
    work; an independent one never on its parent's work. t: a task taken over; parent: a new task's parent."""
    kind = (data["sessions"].get(sid) or {}).get("kind")
    if kind not in CHILD_KINDS:
        return
    x = t if t is not None else (data["tasks"].get(parent) if parent else None)
    if kind == "bound" and (x is None or x["parent"] is None and t is not None or not _parents_work(data, sid, x)):
        raise Refused("you are a BOUND child session agent (%s): you work only on subtasks of your parent's work "
                      "(`sg task new --parent <its task>`); no main task, no other work. Other work: record it as an "
                      "idea (`sg idea add`) or tell the user to start it in another session" % sid)
    if kind == "independent" and x is not None and _parents_work(data, sid, x):
        raise Refused("you are an INDEPENDENT child session agent (%s): your parent's work (%s) is not yours, nor "
                      "its subtasks; it goes to a bound child session or to the session that takes over the parent's "
                      "hand-off" % (sid, x["id"]))


def children_of(data, sid, kind=None):
    """Active child session agents of sid (bound ones with their own bound children, down the chain)."""
    out = []
    for cid in (data["sessions"].get(sid) or {}).get("children") or []:
        c = data["sessions"].get(cid)
        if not c or c["state"] != "active" or c.get("stopped"):
            continue
        k = child_kind(data, cid)
        if kind in (None, k):
            out.append((cid, k))
        if k == "bound":
            out += [x for x in children_of(data, cid, kind) if x not in out]
    return out


def start_handoff(data, sid):
    """The parent begins its hand-off: its bound child session agents must hand off first."""
    rec = data["sessions"].get(sid)
    if rec and not rec.get("handing_off"):
        rec["handing_off"] = core.iso()
    return [(c, missing_handoffs(data, c)) for c, _ in children_of(data, sid, "bound")]


def mark_stopped(data, sid, reason):
    """The session's permanent lock (context, finish, break) seen from the store, so the sessions started from it
    learn it; it blocks nothing (the stopping session still writes its hand-offs)."""
    rec = data["sessions"].get(sid)
    if rec and not rec.get("stopped"):
        rec["stopped"] = {"reason": reason, "at": core.iso()}


def note_identity(data, sid, first_ids=(), long_mode=False):
    """What finds a session again from its messages on another machine (a cloud VM rebuilt, a new ~/.claude): the
    conversation's first assistant message ids, and long mode. Written once each; returns whether it changed."""
    rec = data["sessions"].get(sid)
    if not rec:
        return False
    changed = False
    if first_ids and not rec.get("first_ids"):
        rec["first_ids"] = list(first_ids)[:10]
        changed = True
    if long_mode and not rec.get("long"):
        rec["long"] = True
        changed = True
    return changed


def find_by_messages(data, claude_id, ids, not_sg=None, before=None):
    """The project's session whose conversation this is: the same Claude Code id, or shared first message ids. The
    newest such session wins (a chain of copies keeps the first ids)."""
    known, best = set(ids or ()), None
    for sid, rec in (data or {}).get("sessions", {}).items():
        if sid == not_sg or rec.get("renamed_to") or (before and (rec.get("started") or "") >= before):
            continue
        if (claude_id and claude_id in (rec.get("claude_ids") or [])) or known & set(rec.get("first_ids") or []):
            key = (bool(claude_id and claude_id in (rec.get("claude_ids") or [])), rec.get("started") or "")
            if best is None or key > best[0]:
                best = (key, sid)
    return best and best[1]


def same_conversation(data, fresh, target, claude_id, place):
    """A session found again under a new Claude Code id: the id made for it meanwhile becomes an alias of the old
    one (its $SG_SESSION_ID keeps working), and anything it took is moved over."""
    register_session(data, target, claude_id, place)
    me = data["sessions"].get(fresh)
    if not me or fresh == target:
        return
    for t in data["tasks"].values():
        if t["owner"] == fresh:
            _transfer(data, t, target, "same conversation under a new Claude Code id")
    data["sessions"][fresh] = dict(me, state="renamed", renamed_to=target)


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


def new_task(data, sid, title, kind="normal", priority="P2", parent=None, approval=None, reason=None, related=(),
             queue=False, rule=None, origin=None):
    title = (title or "").strip()
    if not title:
        raise Refused("a task needs a title")
    if queue and parent:
        raise Refused("only a main task waits in the queue; a subtask is opened under its task when work starts")
    if kind not in KINDS:
        raise Refused("kind must be one of " + ", ".join(KINDS))
    if priority not in PRIORITIES:
        raise Refused("priority must be one of " + ", ".join(PRIORITIES))
    _not_handing_off(data, sid)
    _kind_allows(data, sid, parent=parent and task(data, parent)["id"])
    if parent:
        p = task(data, parent)
        if p["status"] in CLOSED:
            raise Refused("%s is %s: a closed task gets no new subtask; open a new task related to it "
                          "(`sg task reopen %s`)" % (parent, p["status"], parent))
        kind = p["kind"] if p["kind"] in EXEMPT else "normal"
    elif queue:
        if kind not in ("normal", "rule-script"):
            raise Refused("only normal and rule-script tasks wait in the queue")
        _no_new_while_urgent(data, sid)
        if not (approval or "").strip():
            raise Refused("a queued task needs the user's approval: --approval \"<the user's words>\"")
        if kind != "rule-script" and queue_count(data) >= MAX_QUEUE:
            raise Refused("%d tasks already wait in the queue. %s" % (MAX_QUEUE, LIMIT_TELL))
    elif kind not in EXEMPT:
        if kind != "urgent":
            _no_new_while_urgent(data, sid)
        if not (approval or "").strip():
            raise Refused("a new active main task needs the user's approval: ask the user, then pass "
                          "--approval \"<the user's words>\". Unrelated work goes to the ideas (`sg idea add`)")
        normal, urgent = main_counts(data)
        limit = normal_limit(data, sid)
        if kind in ("normal", "rule-script") and normal >= limit:
            raise Refused("%d normal main tasks are active (limit %d: %d per working agent). Finish one, queue the "
                          "work (`sg task new --queue`) or record it as an idea (`sg idea add`); if it cannot wait, "
                          "ask the user for an urgent task (--kind urgent --reason ...)" % (normal, limit, MAX_NORMAL))
        if kind == "urgent":
            if not (reason or "").strip():
                raise Refused("an urgent task needs --reason")
            limit = urgent_limit(data, sid)
            if urgent >= limit:
                raise Refused("%d urgent tasks are already active (limit %d: %d per working agent)" % (
                    urgent, limit, MAX_URGENT))
    tid = _new_id(data, parent)
    t = data["tasks"][tid] = {
        "id": tid, "title": title, "kind": kind, "priority": priority, "status": "open", "parent": parent,
        "owner": sid, "owners": [{"session": sid, "from": core.iso(), "to": None, "how": "created"}],
        "done": [], "pending": [], "notes": [], "related": [], "approval": approval, "reason": reason,
        "created": core.iso(), "updated": core.iso(), "completed": None, "handoff": None, "flags": [],
        "rule": rule, "origin": origin}
    if queue:
        t["status"], t["owner"], t["owners"][0]["how"] = QUEUED, None, "queued by %s" % sid
    for rel_id, relation in related:
        task(data, rel_id)
        t["related"].append({"task": rel_id, "relation": relation})
        back = {"task": tid, "relation": "reopened-by" if relation == "reopens" else "extended-by"
                if relation == "extends" else "related"}
        if back not in data["tasks"][rel_id]["related"]:
            data["tasks"][rel_id]["related"].append(back)
    data["events"].append(("task_new", tid))
    return t


def start(data, sid, tid):
    """A queued task becomes active and this session's (the active-task limit applies)."""
    t = task(data, tid)
    if t["status"] != QUEUED:
        raise Refused("%s is %s, not queued" % (tid, t["status"]))
    _not_handing_off(data, sid)
    _kind_allows(data, sid, t=t)
    _no_new_while_urgent(data, sid)
    normal, _ = main_counts(data)
    if normal >= normal_limit(data, sid):
        raise Refused("%d normal main tasks are active (the limit for the agents working now): finish one before "
                      "starting %s" % (normal, tid))
    t["status"] = "open"
    _transfer(data, t, sid, "started from the queue")
    return t


def _transfer(data, t, new_owner, how):
    for o in t["owners"]:
        if o["to"] is None:
            o["to"] = core.iso()
    t["owners"].append({"session": new_owner, "from": core.iso(), "to": None, "how": how})
    t["owner"] = new_owner
    t["updated"] = core.iso()


def claim(data, sid, tid, takeover_approval=None):
    """Takes over a task. Free only when the task carries a hand-off or has no owner (user decision 2026-11-05: no
    agent takes work its owner has not handed off). An owner that is active, ended or locked without a hand-off
    keeps it unless the user approved a takeover. The old owner's history is kept."""
    t = task(data, tid)
    if t["status"] in CLOSED:
        raise Refused("%s is %s. Redoing or extending it is a new task: `sg task reopen %s`" % (tid, t["status"], tid))
    me = data["sessions"].get(sid)
    if me and me["state"] == "locked":
        raise Refused("session %s is locked" % sid)
    if t["owner"] == sid:
        return "already yours"
    _not_handing_off(data, sid)
    _kind_allows(data, sid, t=t)
    old = t["owner"]
    old_rec = data["sessions"].get(old) if old else None
    if t["handoff"]:
        how = "hand-off from %s" % t["handoff"]["source_session"]
    elif old is None:
        how = "claimed (no owner)"
    elif (takeover_approval or "").strip():
        how = "takeover approved by user: %s" % takeover_approval.strip()
    elif old_rec is None or old_rec["state"] in ("ended", "locked"):
        raise Refused("%s is owned by %s, which %s WITHOUT a hand-off. Do not take it: tell the user to open that "
                      "conversation and send any message, so it hands off (break rule) or goes on. Only if the user "
                      "explicitly wants this session to take it anyway: `sg task claim %s --takeover \"<the user's "
                      "words>\"`" % (tid, old, "ended" if not old_rec or old_rec["state"] == "ended" else "is locked",
                                     tid))
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


def close(data, sid, tid, status="done", summary=None, force=False, verified=None):
    """A subtask closing never closes its parent; a parent with open subtasks closes only with force. A rule-script
    task is done only when the script was checked to work (verified); then its CLAUDE.md rule is removed."""
    t = task(data, tid)
    _check_owner(data, sid, t)
    if t["status"] in CLOSED:
        return "already " + t["status"]
    given = " ".join((summary or "").split())
    if len(given) > 200:
        raise Refused("--summary has %d characters; at most 200: one plain sentence" % len(given))
    summary = given
    if not summary and not (t["kind"] == "rule-script" and status == "done" and t.get("rule")):
        raise Refused("pass --summary \"<the result in one plain sentence>\" (cancelled: why): it goes to "
                      "docs/COMPLETED_TASKS.md, from which the project's state is judged later")
    if t["kind"] == "rule-script" and status == "done" and t.get("rule"):
        if not (verified or "").strip():
            raise Refused("%s replaces rule %s with a script: pass --verified \"<how you checked that it works>\" "
                          "(the rule line is removed from CLAUDE.md then)" % (tid, t["rule"]))
        t["done"].append("verified: " + verified.strip())
        summary = summary or "verified: " + " ".join(verified.split())
        r = (data.get("rules") or {}).get(t["rule"])
        if r:
            r["state"], r["replaced_by"] = "replaced by script", tid
        data["events"].append(("rule_replaced", t["rule"]))
    open_kids = [c["id"] for c in children(data, tid) if c["status"] in ACTIVE]
    if open_kids and not force:
        raise Refused("%s has open subtasks (%s): close them first, or pass --force after asking the user" % (
            tid, ", ".join(open_kids)))
    t["status"], t["completed"], t["updated"] = status, core.iso(), core.iso()
    t["result"] = summary
    if summary not in t["done"]:
        t["done"].append(summary)
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


def handoff(data, sid, tid, technical, done=(), pending=(), developer=None, branch=None, pr=None, user_command=None,
            children_gone=None):
    """Writes (or refreshes) the task's hand-off. Not repeated when nothing changed; never for a closed task."""
    t = task(data, tid)
    _check_owner(data, sid, t)
    if t["status"] in CLOSED:
        raise Refused("%s is %s: a finished task needs no hand-off" % (tid, t["status"]))
    if not (technical or "").strip():
        raise Refused("a hand-off needs --technical: what the next session must know to continue (files, commands, "
                      "where it stopped)")
    waiting = [c for c, miss in start_handoff(data, sid) if miss]
    if waiting and not (children_gone or "").strip():
        raise Refused("bound child session agents have not written their hand-offs yet: %s. Run `sg handoff "
                      "children`, send each the message it prints, run it again until all are ready; only then write "
                      "your hand-off. A child that cannot answer: ask the user, then pass --children-gone \"<the "
                      "user's words>\"" % ", ".join(waiting))
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
         "created": core.iso(), "state": "prepared", "push": None,
         "child_handoffs": [{"session": c, "kind": k, "tasks": [t["id"] for t in owned_open(data, c)]}
                            for c, k in children_of(data, sid)]}
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


# --- session notes (user decisions 2026-11-05) ----------------------------------------------------
# One general note per session that handed off work: its signature, date, the quota readings Session Guard holds and
# the agent's short overall assessment. Several can stand at once (several agents). A hand-off note is outdated once
# none of its tasks waits with that session's hand-off any more and a newer note exists: then it goes to the archive as
# one line. An agent whose tasks are all finished writes a "project state" note instead (from COMPLETED_TASKS.md);
# any newer note retires it.
NOTE_MAX = 600


def _note_key(n):
    return "%s@%s" % (n["session"], n["created"])


def _still_waiting(data, sid, tid):
    t = data["tasks"].get(tid)
    return bool(t and t["status"] in ACTIVE and t["handoff"] and t["handoff"]["source_session"] == sid)


def session_note(data, sid, text, quota=None, project_state=False):
    text = " ".join((text or "").split())
    if not text:
        raise Refused("a session note needs --text: your overall assessment in 2-3 sentences (what this session did, "
                      "the state it leaves, what the next agent should look at first)")
    if len(text) > NOTE_MAX:
        raise Refused("the note has %d characters; at most %d: keep it to 2-3 sentences, details belong in the task "
                      "hand-offs" % (len(text), NOTE_MAX))
    if project_state:
        mine = owned_open(data, sid)
        if mine:
            raise Refused("you still own open tasks (%s): finish or hand them off first; a project state note is for "
                          "an agent whose tasks are all finished" % ", ".join(t["id"] for t in mine))
        handed = []
    else:
        handed = sorted((t["id"] for t in data["tasks"].values() if _still_waiting(data, sid, t["id"])), key=sort_key)
        if not handed:
            raise Refused("no hand-off of yours is waiting: write them first (`sg handoff write <id> --technical ...`). "
                          "All your tasks finished: `sg handoff note --project-state --text \"...\"`")
    notes = data.setdefault("session_notes", {})
    old = notes.get(sid)
    if old:
        data.setdefault("notes_retired", []).append(_note_key(old))
    notes[sid] = {"session": sid, "kind": "project-state" if project_state else "hand-off", "created": core.iso(),
                  "tasks": handed, "quota": quota or {}, "text": text}
    retire_notes(data, sid)
    return notes[sid]


def retire_notes(data, newest):
    """Older notes the newest one makes outdated go to the archive (event note_retired, one line each)."""
    notes = data.get("session_notes") or {}
    for s, n in sorted(notes.items()):
        if s == newest or n["created"] > notes[newest]["created"]:
            continue
        if n["kind"] == "project-state" or not any(_still_waiting(data, s, tid) for tid in n["tasks"]):
            data.setdefault("notes_retired", []).append(_note_key(n))
            data["events"].append(("note_retired", s, dict(n), newest))
            del notes[s]


def note_task_state(data, n, tid):
    """For the STATUS view: what happened to a task a hand-off note lists."""
    t = data["tasks"].get(tid)
    if not t:
        return tid
    if _still_waiting(data, n["session"], tid):
        return "%s waiting" % tid
    if t["status"] not in ACTIVE:
        return "%s %s" % (tid, t["status"])
    return "%s taken by %s" % (tid, t["owner"] or "nobody")


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
STATE_ORDER = {"active": 0, "renamed": 1, "ended": 1, "locked": 2}


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
    out["rules"] = dict(theirs.get("rules") or {}, **(ours.get("rules") or {}))
    out["notes_retired"] = list(dict.fromkeys((theirs.get("notes_retired") or []) + (ours.get("notes_retired") or [])))
    gone = set(out["notes_retired"])
    notes = {}
    for side in (theirs, ours):
        for sid, n in (side.get("session_notes") or {}).items():
            if _note_key(n) not in gone and (sid not in notes or n["created"] >= notes[sid]["created"]):
                notes[sid] = n
    out["session_notes"] = notes
    for f in ("next_task", "next_idea", "next_rule", "next_pre20_idea", "rev"):
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
def add_idea(data, sid, text, source=None, rule=None, force=None, prefix="IDEA"):
    """An idea gets a temporary id (IDEA-xxx); when it becomes a task the task id replaces it. At most 50 wait
    (rule-review ideas not counted); a near copy of an existing task or idea is refused. An idea is not a task, so
    it is recorded while an urgent task is active too."""
    text = (text or "").strip()
    if not text:
        raise Refused("an idea needs a text")
    if source != "rule-review" and idea_count(data) >= MAX_IDEAS:
        raise Refused("%d ideas already wait. %s" % (MAX_IDEAS, LIMIT_TELL))
    dup = [f for f in similar(data, text, 0.85)]
    if dup and not (force or "").strip():
        raise Refused("this is a near copy of %s (%s, %s): \"%s\". Do not add it again; if the user says it is "
                      "really new, pass --force \"<the user's words>\"" % (dup[0][1], dup[0][2], dup[0][3], dup[0][4]))
    key = "next_idea" if prefix == "IDEA" else "next_" + prefix.lower().replace("-", "_")
    n = data.get(key) or 1
    iid = "%s-%03d" % (prefix, n)
    while iid in data["ideas"]:
        n += 1
        iid = "%s-%03d" % (prefix, n)
    data[key] = n + 1
    data["ideas"][iid] = {"id": iid, "text": text, "by": sid, "added": core.iso(), "source": source,
                          "state": "waiting", "task": None, "rule": rule}
    return iid


def promote_idea(data, sid, iid, approval, priority="P2", queue=False):
    idea = data["ideas"].get(iid)
    if not idea or idea["state"] != "waiting":
        raise Refused("no waiting idea " + iid)
    rule_work = idea.get("source") == "rule-review"
    t = new_task(data, sid, idea["text"], kind="rule-script" if rule_work else "normal", priority=priority,
                 approval=approval, queue=queue, rule=idea.get("rule"), origin="idea %s" % iid)
    idea["state"], idea["task"] = "promoted", t["id"]  # the task id replaces the temporary idea id
    return t
