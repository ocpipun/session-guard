# SESSION-GUARD: 2.0 | component: sg2-cli | version: 2.0.0
"""`sg` commands (python session_guard.py <command> ...). Output is plain text for Claude; exit 1 = refused."""
import argparse
import glob
import json
import os
import re
import sys

from . import SG_VERSION, core, gitops, legacy, manifest, messages, migrate, place, render, rules, store

ENTRY = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "session_guard.py")
PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def out(text):
    sys.stdout.buffer.write((text + "\n").encode("utf-8"))
    sys.stdout.flush()


def root_of(args):
    r = getattr(args, "root", None) or os.environ.get("SG_PROJECT_ROOT") or os.environ.get("CLAUDE_PROJECT_DIR") \
        or os.getcwd()
    return core.main_checkout(core.project_root({"cwd": r}) if not getattr(args, "root", None) else os.path.abspath(r))


def session_of(args):
    sid = getattr(args, "session", None) or os.environ.get("SG_SESSION_ID")
    if not sid or not core.SESSION_ID.match(sid):
        raise store.Refused("no SESSION-ID: pass --session S-YYYYMMDD-xxxxxx (given at session start)")
    return sid


def show_task(data, t):
    lines = ["%s · %s · %s · %s · %s · owner %s" % (t["id"], t["title"], t["kind"], t["status"], t["priority"],
                                                  t["owner"] or "none"),
             "parent: %s · children: %s" % (t["parent"] or "-", ", ".join(c["id"] for c in store.children(data, t["id"]))
                                            or "-"),
             "done: " + ("; ".join(t["done"]) or "-"), "pending: " + ("; ".join(t["pending"]) or "-"),
             "related: " + (", ".join("%s (%s)" % (r["task"], r["relation"]) for r in t["related"]) or "-"),
             "owners: " + " → ".join("%s [%s, %s]" % (o["session"], o["how"], o["from"][:10]) for o in t["owners"])]
    lines += ["note: " + n for n in t["notes"][-5:]]
    if t["handoff"]:
        lines += ["HAND-OFF:"] + ["  " + l for l in render.handoff_lines(t["handoff"])[:-1]]
    if t.get("pending_user_command"):
        lines.append("user command waiting (word for word): " + t["pending_user_command"])
    return "\n".join(lines)


def cmd_task(a):
    root = root_of(a)
    if a.action == "list":
        data = store.load(root)
        if not data:
            raise store.Refused("not a 2.0 project: " + root)
        out("%s %s rev %d" % (data["project_id"], root, data["rev"]))
        for t in sorted(data["tasks"].values(), key=lambda t: store.sort_key(t["id"])):
            if a.all or t["status"] in store.ACTIVE:
                out("%s%s · %s · %s · %s · owner %s%s" % ("  " * t["id"].count("-", 5), t["id"], t["title"], t["status"],
                                                       t["priority"], t["owner"] or "none",
                                                       " · hand-off" if t["handoff"] else ""))
        return 0
    if a.action == "show":
        data = store.load(root)
        out(show_task(data, store.task(data, a.id)))
        return 0
    sid = session_of(a)
    exp = a.expect_rev
    if a.action == "new":
        t = store.mutate(root, lambda d: store.new_task(d, sid, a.title, kind=a.kind, priority=a.priority,
                                                         parent=a.parent, approval=a.approval, reason=a.reason),
                         expect_rev=exp)
        out("created %s (%s) owner %s" % (t["id"], t["title"], sid))
    elif a.action == "claim":
        how = store.mutate(root, lambda d: store.claim(d, sid, a.id, a.takeover), expect_rev=exp)
        out("%s: %s" % (a.id, how))
    elif a.action == "note":
        store.mutate(root, lambda d: store.note(d, sid, a.id, done=a.done or [], pending=a.pending or [],
                                                 resolved=a.resolved or [], status=a.status, priority=a.priority,
                                                 text=a.text), expect_rev=exp)
        out("%s updated" % a.id)
    elif a.action in ("done", "cancel"):
        r = store.mutate(root, lambda d: store.close(d, sid, a.id, "done" if a.action == "done" else "cancelled",
                                                      summary=a.summary, force=a.force), expect_rev=exp)
        out("%s %s" % (a.id, r if isinstance(r, str) else r["status"]))
    elif a.action == "reopen":
        t = store.mutate(root, lambda d: store.reopen(d, sid, a.id, a.title, a.relation, a.approval), expect_rev=exp)
        out("created %s (%s %s)" % (t["id"], a.relation, a.id))
    elif a.action == "relate":
        store.mutate(root, lambda d: store.relate(d, sid, a.id, a.other, a.relation))
        out("related %s and %s" % (a.id, a.other))
    return 0


def cmd_idea(a):
    root = root_of(a)
    sid = session_of(a)
    if a.action == "add":
        iid = store.mutate(root, lambda d: store.add_idea(d, sid, a.text))
        out("backlog: %s" % iid)
    else:
        t = store.mutate(root, lambda d: store.promote_idea(d, sid, a.id, a.approval))
        out("%s → %s" % (a.id, t["id"]))
    return 0


def cmd_handoff(a):
    root = root_of(a)
    sid = session_of(a)
    if a.action == "write":
        br = gitops.branch(root) if gitops.is_repo(root) else None
        pr = a.pr or (gitops.pr_url(root) if br else None)
        r = store.mutate(root, lambda d: store.handoff(d, sid, a.id, a.technical, done=a.done or [],
                                                        pending=a.pending or [], developer=a.developer, branch=br,
                                                        pr=pr))
        out("%s hand-off %s" % (a.id, r))
        return 0
    # publish: commit Session Guard's own files and verify the push
    if gitops.is_repo(root):
        data = store.load(root)
        c = gitops.commit_paths(root, "Session Guard 2.0: hand-off from %s (%s)\n\nSESSION-GUARD: 2.0" % (
            sid, data["project_id"]))
        if c.get("error"):
            raise store.Refused("commit failed: " + c["error"])
        push = gitops.push_verified(root)
    else:
        push = {"verified": False, "why": "not a git repository: hand-off kept in this folder only"}
    n = store.mutate(root, lambda d: store.mark_published(d, sid, push))
    if gitops.is_repo(root) and n:
        gitops.commit_paths(root, "Session Guard 2.0: hand-off state (%s)\n\nSESSION-GUARD: 2.0" % sid)
        if push.get("verified"):
            push = gitops.push_verified(root)
    out("hand-offs marked: %d · push %s%s" % (n, "VERIFIED %s" % push["commit"][:12] if push.get("verified") else
                                              "NOT VERIFIED", "" if push.get("verified") else " (%s)" % push.get("why")))
    return 0 if push.get("verified") or not gitops.is_repo(root) else 1


def cmd_migrate(a):
    if a.action == "discover":
        added = migrate.discover(a.add or [])
        out(migrate.summary())
        out("new: %d" % len(added))
    elif a.action == "status":
        out(migrate.summary())
    elif a.action == "survey":
        p, facts = migrate.survey(a.path)
        out("survey written: %s (%d instruction files, %d status-like files, %d old sessions)" % (
            p, len(facts["instruction_files"]), len(facts["status_like_files"]), len(facts["sessions"])))
    elif a.action == "plan-help":
        out(migrate.PLAN_HELP)
    elif a.action == "apply":
        plan = core.read_json(a.plan)
        sid = session_of(a)
        migrate.add_session(os.environ.get("SG_CLAUDE_SESSION") or sid)
        pid, rep = migrate.apply(a.path, plan, sid)
        out("applied: %s %s; rollback: %s; warnings: %s" % (a.path, pid, (rep["rollback"] or {}).get("tag", "none"),
                                                           "; ".join(rep["warnings"]) or "none"))
    elif a.action == "verify":
        problems = migrate.verify(a.path)
        out("verified" if not problems else "NOT VERIFIED:\n  " + "\n  ".join(problems))
        return 1 if problems else 0
    elif a.action == "push":
        r = migrate.push(a.path, a.approval)
        out("push %s%s" % ("VERIFIED " + r["commit"][:12] if r.get("verified") else "NOT VERIFIED",
                           "" if r.get("verified") else ": " + str(r.get("why"))))
        return 0 if r.get("verified") else 1
    elif a.action == "pause":
        info = migrate.pause(a.reason or "paused by Claude")
        out("installation paused; left: %s. Continue with: continue installation" % ", ".join(info["left"]))
    elif a.action == "skip":
        migrate.skip(a.path, a.reason or "user decision")
        out("skipped " + a.path)
    elif a.action == "finish":
        migrate.finish()
        out("migration done")
    elif a.action == "start":
        migrate.add_session(a.claude_session)
        out("migration session recorded: %s" % a.claude_session)
    return 0


def cmd_install(a):
    """Install / uninstall steps run by skill setup after the user's approval."""
    cfg = core.load_config()
    if a.action == "approve":
        if os.path.basename(os.path.abspath(a.executive)).lower() != "session-guard-executive":
            raise store.Refused("the control project must be a folder named session-guard-executive")
        res = rules.ensure_global_block()
        cfg.update(installed=True, rules=rules.RULES_VERSION, version=SG_VERSION, date=core.today(),
                   executive=os.path.abspath(a.executive))
        core.save_config(cfg)
        out("installed: config %s; global block %s; 1.x block %s; status line: %s" % (
            core.config_path(), res, rules.remove_v1_block(), place.install_statusline(ENTRY)))
    elif a.action == "uninstall":
        cfg.pop("installed", None)
        core.save_config(cfg)
        out("uninstalled: global block %s; status line %s (project files and stores are kept)" % (
            rules.remove_global_block(), place.uninstall_statusline()))
    elif a.action == "cloud":
        out("cloud copy: %s" % place.install_copy(os.path.abspath(a.code or os.getcwd())))
    elif a.action == "home-guard":
        out("computer guard: %s" % ("added" if place.ensure_home_guard(os.path.abspath(a.code)) else "unchanged"))
    elif a.action == "conflicts":
        rows = legacy.conflicts()
        out("\n".join("%-60s %-22s %s" % r for r in rows) or "no Session Guard 1.x components found")
    elif a.action == "language":
        code = a.code or messages.app_locale()
        if not code:
            out('app language not found: ask the user "Which language would you like session-guard notices in?"')
            return 1
        loc = messages.set_locale(code)
        out("notice language: %s; untranslated: %s" % (loc or "English", ", ".join(messages.missing(loc)) or "none"))
    elif a.action == "messages":
        loc = core.load_config().get("locale")
        bad = messages.store_translation(loc, core.read_json(a.code, {}) or {}) if loc else ["(no language chosen)"]
        out("stored; rejected: %s" % (", ".join(bad) or "none"))
    return 0


def cmd_manifest(a):
    if a.action == "build":
        data = manifest.build(PLUGIN_ROOT)
        out("manifest: %d components" % len(data["components"]))
        return 0
    problems = manifest.verify(PLUGIN_ROOT)
    out("manifest OK (%s)" % SG_VERSION if not problems else "MANIFEST PROBLEMS:\n  " + "\n  ".join(problems))
    return 1 if problems else 0


def report(project_dir):
    """One line for "finish session" (v1.4): requests, cache read/write, output, largest context of the latest
    session of a project."""
    folder = migrate.transcript_folder(os.path.abspath(project_dir or os.getcwd()))
    files = glob.glob(os.path.join(folder, "*.jsonl"))
    if not files:
        out("no transcript: " + folder)
        return 1
    p = max(files, key=os.path.getmtime)
    usage, order = {}, []
    with open(p, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                e = json.loads(line)
            except ValueError:
                continue
            m = e.get("message") or {}
            if e.get("type") == "assistant" and isinstance(m, dict) and m.get("id") and m.get("usage"):
                if m["id"] not in usage:
                    order.append(m["id"])
                usage[m["id"]] = m["usage"]
    rd = wr = op = big = 0
    for mid in order:
        u = usage[mid]
        r, w = u.get("cache_read_input_tokens") or 0, u.get("cache_creation_input_tokens") or 0
        rd, wr, op = rd + r, wr + w, op + (u.get("output_tokens") or 0)
        big = max(big, r + w + (u.get("input_tokens") or 0))
    out("session=%s requests=%d cache_read=%d cache_write=%d output=%d largest_context=%d" % (
        os.path.basename(p)[:8], len(order), rd, wr, op, big))
    return 0


def parser():
    p = argparse.ArgumentParser(prog="sg", description="Session Guard %s" % SG_VERSION)
    sub = p.add_subparsers(dest="cmd", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--session")
    common.add_argument("--root")
    common.add_argument("--expect-rev", type=int)

    t = sub.add_parser("task", parents=[common])
    t.add_argument("action", choices=["list", "show", "new", "claim", "note", "done", "cancel", "reopen", "relate"])
    t.add_argument("id", nargs="?")
    t.add_argument("--title")
    t.add_argument("--kind", default="normal")
    t.add_argument("--priority")
    t.add_argument("--parent")
    t.add_argument("--approval")
    t.add_argument("--reason")
    t.add_argument("--takeover")
    t.add_argument("--done", action="append")
    t.add_argument("--pending", action="append")
    t.add_argument("--resolved", action="append")
    t.add_argument("--status")
    t.add_argument("--text")
    t.add_argument("--summary")
    t.add_argument("--force", action="store_true")
    t.add_argument("--relation", default="reopens")
    t.add_argument("--other")
    t.add_argument("--all", action="store_true")

    i = sub.add_parser("idea", parents=[common])
    i.add_argument("action", choices=["add", "promote"])
    i.add_argument("text_or_id")
    i.add_argument("--approval")

    h = sub.add_parser("handoff", parents=[common])
    h.add_argument("action", choices=["write", "publish"])
    h.add_argument("id", nargs="?")
    h.add_argument("--technical")
    h.add_argument("--done", action="append")
    h.add_argument("--pending", action="append")
    h.add_argument("--developer")
    h.add_argument("--pr")

    m = sub.add_parser("migrate", parents=[common])
    m.add_argument("action", choices=["discover", "status", "survey", "plan-help", "apply", "verify", "push", "pause",
                                      "skip", "finish", "start"])
    m.add_argument("path", nargs="?")
    m.add_argument("--add", action="append")
    m.add_argument("--plan")
    m.add_argument("--approval")
    m.add_argument("--reason")
    m.add_argument("--claude-session")

    n = sub.add_parser("install")
    n.add_argument("action", choices=["approve", "uninstall", "conflicts", "language", "messages", "cloud",
                                      "home-guard"])
    n.add_argument("code", nargs="?")
    n.add_argument("--executive")

    f = sub.add_parser("manifest")
    f.add_argument("action", choices=["build", "verify"])

    r = sub.add_parser("report")
    r.add_argument("path", nargs="?")
    return p


def run(argv):
    a = parser().parse_args(argv)
    if a.cmd == "task" and a.action == "new" and not a.priority:
        a.priority = "P2"
    if a.cmd == "task" and a.action == "reopen" and a.relation not in ("reopens", "extends"):
        a.relation = "reopens"
    if a.cmd == "idea":
        a.text, a.id = a.text_or_id, a.text_or_id
    try:
        if a.cmd == "task":
            if a.action in ("show", "claim", "note", "done", "cancel", "reopen", "relate") and not \
                    (a.id and core.TASK_ID.match(a.id)):
                raise store.Refused("give a TASK-ID")
            return cmd_task(a)
        if a.cmd == "idea":
            return cmd_idea(a)
        if a.cmd == "handoff":
            return cmd_handoff(a)
        if a.cmd == "migrate":
            return cmd_migrate(a)
        if a.cmd == "install":
            return cmd_install(a)
        if a.cmd == "manifest":
            return cmd_manifest(a)
        if a.cmd == "report":
            return report(a.path)
    except (store.Refused, core.Busy) as e:
        out("REFUSED: %s" % e)
        return 1
    return 2
