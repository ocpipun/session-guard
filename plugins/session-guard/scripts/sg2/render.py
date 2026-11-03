# SESSION-GUARD: 2.0 | component: sg2-render | version: 2.0.0
"""Project files rendered from the store.

docs/STATUS.md          the generated block (project, active tasks, hand-offs) between markers; text outside the
                        block (a "Notes" section) is the project's own and is kept
docs/TASK_BACKLOG.md    generated in full from the ideas
docs/COMPLETED_TASKS.md one appended line per finished task
docs/STATUS_ARCHIVE.md  appended only (claimed hand-offs, locks, migration records); never rewritten, so a large
                        archive is never loaded or written in one go
"""
import hashlib
import os
import re

from . import core

STATUS_MAX_LINES = 50
SIGN = "[SESSION-GUARD: 2.0]"
BEGIN = "<!-- SESSION-GUARD: 2.0 | sg-status:begin | generated from .claude/session-guard2/store.json: change it " \
        "with `sg` commands, not by hand -->"
END_RE = re.compile(r"<!-- sg-status:end sha=([0-9a-f]{12}) -->")
STATUS_HEAD = "# STATUS — %s\n\n"
NOTES = "\n## Notes\n- None\n"


def doc(root, name):
    return core.exists_ci(root, "docs/" + name) or os.path.join(root, "docs", name)


def _sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def _owner(data, sid):
    if not sid:
        return "no owner"
    rec = data["sessions"].get(sid)
    return "%s%s" % (sid, "" if not rec or rec["state"] == "active" else " (%s)" % rec["state"])


def _short(items, keep):
    if len(items) <= keep:
        return "; ".join(items) or "-"
    return "; ".join(items[-keep:]) + " (+%d more: `sg task show`)" % (len(items) - keep)


def block_lines(data, compact=0):
    """compact 0: full; 1: shorter lists; 2: one line per task."""
    from . import store
    normal, urgent = store.main_counts(data)
    lines = ["## Project %s" % SIGN,
             "- PROJECT-ID: %s · home: %s · store rev %d" % (data["project_id"], data["home"], data["rev"]),
             "", "## Active tasks (normal %d/%d · urgent %d/%d)" % (normal, store.MAX_NORMAL, urgent, store.MAX_URGENT)]
    mains = sorted((t for t in data["tasks"].values() if t["parent"] is None and t["status"] in store.ACTIVE),
                   key=lambda t: (t["kind"] not in store.EXEMPT, t["priority"], store.sort_key(t["id"])))
    if not mains:
        lines.append("- None")
    keep = (3, 2, 0)[compact]
    for t in mains:
        flag = " · ORPHANED (owner ended without hand-off)" if "orphaned" in t["flags"] else ""
        lines.append("### %s · %s · %s · %s%s · owner %s%s" % (
            t["id"], t["title"], t["status"], t["priority"], "" if t["kind"] == "normal" else " · " + t["kind"],
            _owner(data, t["owner"]), flag))
        if compact < 2:
            lines.append("- Done: " + _short(t["done"], keep))
            lines.append("- Pending: " + _short(t["pending"], keep + 1))
        kids = [c for c in _descendants(data, t["id"]) if c["status"] in store.ACTIVE]
        if kids:
            if compact:
                lines.append("- Subtasks open: %d (%s)" % (len(kids), ", ".join(c["id"] for c in kids)))
            else:
                for c in kids:
                    lines.append("- %s %s · %s · owner %s" % (c["id"], c["title"], c["status"], _owner(data, c["owner"])))
        if t["related"] and compact < 2:
            lines.append("- Related: " + ", ".join("%s (%s)" % (r["task"], r["relation"]) for r in t["related"]))
    waiting = sorted(store.waiting_handoffs(data), key=lambda t: store.sort_key(t["id"]))
    lines += ["", "## Hand-offs waiting (%d)" % len(waiting)]
    if not waiting:
        lines.append("- None")
    for t in waiting:
        h = t["handoff"]
        push = h.get("push") or {}
        lines.append("- %s from %s, %s, %s%s: claim with `sg task claim %s`" % (
            t["id"], h["source_session"], h["created"][:10], h["state"],
            " (%s)" % push.get("commit", "")[:8] if push.get("commit") else "", t["id"]))
    return lines


def _descendants(data, tid):
    from . import store
    out = []
    for c in store.children(data, tid):
        out.append(c)
        out += _descendants(data, c["id"])
    return out


def compose_block(data, outside_lines):
    for compact in (0, 1, 2):
        body = "\n".join(block_lines(data, compact))
        if len(body.splitlines()) + outside_lines + 3 <= STATUS_MAX_LINES:
            break
    return BEGIN + "\n" + body + "\n<!-- sg-status:end sha=%s -->" % _sha(body)


def split_status(text):
    """(before, block body or None, after) of a STATUS.md."""
    a = text.find(BEGIN.split(" | ")[0] + " | sg-status:begin")
    m = END_RE.search(text)
    if a < 0 or not m or m.start() < a:
        return text, None, ""
    head_end = text.find("\n", a)
    return text[:a], text[head_end + 1:m.start()].rstrip("\n"), text[m.end():]


def block_intact(root):
    """False when someone edited the generated block by hand (its hash no longer matches)."""
    text = core.read_text(doc(root, "STATUS.md"))
    if text is None:
        return True
    _, body, _ = split_status(text)
    if body is None:
        return False
    m = END_RE.search(text)
    return _sha(body) == m.group(1)


def render_status(root, data):
    path = doc(root, "STATUS.md")
    text = core.read_text(path)
    if text is None:
        before, after = STATUS_HEAD % data["name"], NOTES
    else:
        before, body, after = split_status(text)
        if body is None:  # a file without the block: the block goes under its title, the rest stays
            first, _, rest = text.partition("\n")
            before, after = (first + "\n\n", "\n" + rest.lstrip("\n")) if first.startswith("# ") else \
                (STATUS_HEAD % data["name"], "\n" + text)
    outside = len(before.splitlines()) + len(after.splitlines())
    new = before + compose_block(data, outside) + after
    if not new.endswith("\n"):
        new += "\n"
    if new != text:
        core.write_text(path, new)
    return len(new.splitlines())


def status_lines(root):
    text = core.read_text(doc(root, "STATUS.md"))
    return len(text.splitlines()) if text else 0


def render_backlog(root, data):
    lines = ["# TASK BACKLOG", "", "<!-- SESSION-GUARD: 2.0 | component: task-backlog | generated from the store: "
             "add with `sg idea add`, start with `sg idea promote` (needs the user's approval) -->", "",
             "## Waiting ideas %s" % SIGN]
    waiting = [i for i in sorted(data["ideas"].values(), key=lambda i: i["id"]) if i["state"] == "waiting"]
    lines += ["- %s · %s · %s · %s" % (i["id"], i["added"][:10], i["by"], i["text"]) for i in waiting] or ["- None"]
    promoted = [i for i in data["ideas"].values() if i["state"] == "promoted"]
    if promoted:
        lines += ["", "## Promoted"] + ["- %s → %s" % (i["id"], i["task"]) for i in promoted]
    core.write_text(doc(root, "TASK_BACKLOG.md"), "\n".join(lines) + "\n")


COMPLETED_HEAD = ("# COMPLETED TASKS\n\nOne line per finished task, appended by Session Guard 2.0. Details: the "
                  "task record (`sg task show <id>`) and STATUS_ARCHIVE.md.\n\n")
ARCHIVE_HEAD = "# STATUS archive\n\nOne dated entry per item. Not read every session: find the heading by search.\n\n"


def append_completed(root, data, tid):
    t = data["tasks"][tid]
    path = doc(root, "COMPLETED_TASKS.md")
    if not os.path.exists(path):
        core.append_text(path, COMPLETED_HEAD)
    owners = ", ".join(dict.fromkeys(o["session"] for o in t["owners"]))
    rel = "; related " + ", ".join("%s (%s)" % (r["task"], r["relation"]) for r in t["related"]) if t["related"] else ""
    core.append_text(path, "- %s %s %s %s · %s · %s · owners %s%s\n" % (
        core.today(), SIGN, data["project_id"], tid, t["title"], t["status"], owners, rel))


def append_archive(root, title, lines):
    path = doc(root, "STATUS_ARCHIVE.md")
    if not os.path.exists(path):
        core.append_text(path, ARCHIVE_HEAD)
    core.append_text(path, "\n## %s %s %s\n" % (core.today(), SIGN, title) + "".join("- %s\n" % l for l in lines))


def handoff_lines(h):
    return ["PROJECT-ID: %s · TASK-ID: %s" % (h["project_id"], h["task_id"]),
            "Source session: %s (parent: %s; children: %s)" % (h["source_session"], h.get("session_parent") or "none",
                                                               ", ".join(h.get("session_children") or []) or "none"),
            "Last developer: %s · status: %s · owner then: %s" % (h["last_developer"], h["status"], h["owner"]),
            "Done: " + ("; ".join(h["done"]) or "-"), "Pending: " + ("; ".join(h["pending"]) or "-"),
            "Branch: %s · PR: %s" % (h.get("branch") or "-", h.get("pr") or "-"),
            "Technical: " + h["technical"],
            "User command (word for word): " + (h.get("user_command") or "none"),
            "Written %s · %s%s" % (h["created"], h["state"], " · push %s" % (h.get("push") or {}).get("commit", "")
                                   if h.get("push") else ""),
            "Claimed by %s at %s" % (h.get("claimed_by"), h.get("claimed_at"))]


def after_change(root, data, events):
    for ev in events:
        if ev[0] == "task_closed":
            append_completed(root, data, ev[1])
        elif ev[0] == "handoff_claimed":
            append_archive(root, "Hand-off %s from %s (claimed)" % (ev[1], ev[2]["source_session"]), handoff_lines(ev[2]))
        elif ev[0] == "session_locked":
            append_archive(root, "Session %s locked" % ev[1], [ev[2]])
    render_status(root, data)
    render_backlog(root, data)
    if not os.path.exists(doc(root, "COMPLETED_TASKS.md")):
        core.append_text(doc(root, "COMPLETED_TASKS.md"), COMPLETED_HEAD)
