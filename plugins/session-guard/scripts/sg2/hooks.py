# SESSION-GUARD: 2.0 | component: sg2-hooks | version: 2.0.0
"""Hook handlers: SessionStart, UserPromptSubmit (+ the 24-hour watch), PreToolUse, PostToolUse, Stop, SessionEnd.

Verified against the official hook docs (code.claude.com/docs/en/hooks.md, 2026-11-03):
- UserPromptSubmit {"decision": "block"} stops the prompt before it reaches Claude; the reason is shown to the
  user. The prompt itself is still written to the transcript (it is not erased).
- SessionStart cannot block: a session that must not work is locked there and blocked at its first prompt.
- PostToolUse {"continue": false, "stopReason"} stops Claude; PreToolUse permissionDecision "deny" refuses a tool.
- Stop {"decision": "block"} keeps Claude working (used to finish hand-offs after "finish session").
- SessionEnd cannot block and has a short budget: it only records the end.
- CLAUDE_ENV_FILE (SessionStart) passes SG_SESSION_ID to Claude's Bash commands.
- Hooks also fire inside subagents (agent_id present): those calls never trigger notices or locks.
On any error a hook exits 0 silently: it must never break the user's work.
"""
import glob
import json
import os
import re
import sys
import time

from . import SG_VERSION, core, gitops, legacy, measure, messages, migrate, render, rules, sessions, store
from .messages import msg

ENTRY = None          # set by session_guard.py
PROJECT_MODE = False  # repository copy (cloud)
WATCH_SECONDS = int(os.environ.get("SESSION_GUARD_WATCH_SECONDS") or 24 * 3600)
WATCH_STEP = 60
BREAK_WAKE = int(os.environ.get("SESSION_GUARD_BREAK_WAKE") or 55 * 60)  # tests shorten it
EXECUTIVE = "session-guard-executive"
STORE_DIR_MARK = core.PROJECT_DIRNAME.replace("\\", "/")
STALE_JOBS = ("WARNING (24 hours): %d background job(s) or subagent(s) started over 24 hours ago never reported back. "
              "Tell the user in one line: a bug may have occurred. Check the job if you can. If it is stuck, suggest "
              "\"finish session\".")
LONG_TURN = ("WARNING (24 hours): you have been working on the same user message for over 24 hours. Tell the user: a "
             "bug may have occurred. If you think there is a problem, suggest \"finish session\".")
WATCH_WAKE = ("[Session Guard] 24-HOUR WATCH: 24 hours have passed since the user's last message and the session is "
              "still busy. Tell the user: a bug may have occurred. If you think there is a problem, suggest \"finish "
              "session\".")


def emit(data):
    sys.stdout.write(json.dumps(data, ensure_ascii=True))
    return 0


def ctx(event, text, user=None, **extra):
    data = {"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}}
    if user:
        data["systemMessage"] = user
    data.update(extra)
    return data


def sg_cmd():
    return '%s "%s"%s' % ("python" if os.name == "nt" else "python3", ENTRY, " --project" if PROJECT_MODE else "")


def measure_todo(root):
    """Session measurement (v1.4 "finish session"): also after the 300k / 800k hand-off, where finish is refused."""
    return (' SESSION MEASUREMENT: run %s report "%s", read the weekly and 5-hour quota with the usage tool if there '
            'is one, and add one row to "Session measurements" in docs/STATUS_ARCHIVE.md ("n/a" for a value you '
            'cannot read; never invent one).' % (sg_cmd(), root or os.getcwd()))


def setup_marker(root):
    return os.path.join(core.project_dir(root), "setup.json")


def activation(root):
    """'active', 'v1' (1.x still installed: stay passive), 'setup' or 'executive' (control project missing)."""
    if PROJECT_MODE:
        m = core.read_json(setup_marker(root), {}) if root else {}
        return "active" if (m or {}).get("rules") == rules.RULES_VERSION else "setup"
    if legacy.v1_active():
        return "v1"
    cfg = core.load_config()
    if not cfg.get("installed") or cfg.get("rules") != rules.RULES_VERSION:
        return "setup"
    if not cfg.get("executive") or not os.path.isdir(cfg["executive"]):
        return "executive"
    return "active"


def inactive(act, event):
    if act == "v1":
        return ctx(event, "[Session Guard 2.0] PASSIVE: Session Guard 1.x is still installed and guards this session.",
                   msg("v1_still_installed") if event == "SessionStart" else None)
    if act == "executive":
        return ctx(event, "[Session Guard 2.0] OFF on this computer: its control project (a folder named %s) is "
                          "missing. Do not apply Session Guard rules. Tell the user in one line." % EXECUTIVE)
    return ctx(event, "[Session Guard 2.0] SETUP PENDING: installed but its setup is not done (or its rules changed). "
                      "Until the user approves, it enforces nothing. Run skill setup (\"Install\") now. Command: %s"
               % sg_cmd(), msg("setup_pending") if event == "SessionStart" else None)


def is_executive(root):
    return bool(root) and os.path.basename(root).lower() == EXECUTIVE


def subagent(inp):
    return bool(inp.get("agent_id"))


PARENT_MARK = re.compile(r"^\s*SG-PARENT:\s*(S-\d{8}-[0-9a-f]{6}(?:-[BI]\d+)*)[ \t]*(bound|independent)?\s*$",
                         re.M | re.I)
CHILD_TEXT = {
    "bound": "a BOUND child session agent: you work only on subtasks of %s's work (`sg task new --parent <its "
             "task>`), never a main task of your own. When %s hands off, you hand off first and this session is "
             "locked with it.",
    "independent": "an INDEPENDENT child session agent: your own main tasks (with the user's approval words from the "
                   "brief), never %s's work or its subtasks. If %s stops, you go on; Session Guard then tells you."}


def child_session(root, rec, prompt):
    """A session started from another one: its brief carries the line "SG-PARENT: <id> bound|independent". Before
    it owns anything it gets the id <parent>-B<n> or <parent>-I<n>; the first id stays an alias."""
    m = PARENT_MARK.search(prompt or "")
    if not (m and root and rec.get("sg_id")):
        return None
    if not m.group(2):
        return ("[Session Guard 2.0] The SG-PARENT line has no kind, so this session is NOT a child session of %s. "
                "Tell the user in one line: the line must end with `bound` (it helps with the parent's work and is "
                "locked with it) or `independent` (its own work, it goes on); they can send it again." % m.group(1))
    old, kind = rec["sg_id"], m.group(2).lower()
    try:
        new = store.mutate(root, lambda d: store.make_child(d, old, m.group(1), kind), render=False)
    except (store.Refused, core.Busy):
        return None
    if not new:
        return None
    rec["sg_id"] = new
    return ("[Session Guard 2.0] CHILD SESSION: your SESSION-ID is %s (started from %s; %s stays its alias, so "
            "$SG_SESSION_ID keeps working). You are %s The same rules as any session apply: limits, thresholds and "
            "hand-offs." % (new, m.group(1), old, CHILD_TEXT[kind] % (m.group(1), m.group(1))))


def parent_note(data, rec):
    """Once, when the session this one was started from hands off or stops. A bound child session agent hands off
    and is locked with it; an independent one is told and goes on; a continuation took over its work: no note."""
    sid = rec.get("sg_id") or ""
    me = (data or {}).get("sessions", {}).get(sid) or {}
    par = data["sessions"].get(me.get("parent") or "") if me else None
    if not par or "parent_handoff" in rec["notified"] or "parent_stopped" in rec["notified"] or \
            store.child_kind(data, sid) == "continuation":
        return None
    why = ("handing off" if par.get("handing_off") else None) or (par.get("stopped") or {}).get("reason") or \
        (par.get("lock_reason") if par["state"] == "locked" else "ended" if par["state"] == "ended" else None)
    if not why:
        return None
    if store.child_kind(data, sid) == "bound":
        rec["notified"].append("parent_handoff")
        return ("[Session Guard 2.0] PARENT SESSION %s HANDS OFF (%s). You are a BOUND child session agent (you own "
                "only subtasks of its work): stop the work now. Bring each task you own to a hand-off-ready point "
                "and write its hand-off (`%s handoff write <id> --technical \"...\"`: branch and PR are recorded; add "
                "--pr <link> if missing), then your session note (`%s handoff note --text \"...\"`), then `%s handoff publish`. "
                "Do nothing else: this session is locked once every hand-off is written. Tell the user in one line."
                % (par["id"], why, sg_cmd(), sg_cmd(), sg_cmd()))
    rec["notified"].append("parent_stopped")
    return ("[Session Guard 2.0] PARENT SESSION %s HANDS OFF / STOPPED (%s). You are an INDEPENDENT child session "
            "agent (your own main task): you go on under the same rules; its reports no longer reach it. Keep your "
            "task notes current (`%s task note`), so the next session of the parent's work sees where you are." % (
                par["id"], why, sg_cmd()))


def with_parent_lock(rec, data):
    """A bound child session agent is locked as soon as every task it owns has its hand-off."""
    if "parent_handoff" not in rec["notified"] or not data or store.missing_handoffs(data, rec.get("sg_id")):
        return False
    me = data["sessions"].get(rec.get("sg_id") or "") or {}
    sessions.lock(rec, "handed", "handed off with its parent %s" % me.get("parent"), by=me.get("parent"))
    return True


def stop(root, rec, kind, reason):
    """A permanent lock (context, finish, break), also marked in the store for the sessions started from this one."""
    sessions.lock(rec, kind, reason)
    if root and rec.get("sg_id") and store.is_project(root):
        try:
            store.mutate(root, lambda d: store.mark_stopped(d, rec["sg_id"], "%s: %s" % (kind, reason)), render=False)
        except (store.Refused, core.Busy):
            pass


# --- identity: a conversation found again from its messages (user decision 2026-11-05) ----------------------
# Claude Code's session_id is not a stable handle: the desktop app was seen copying a conversation to a new id
# (2026-11-04, 2026-11-05) and running its first hook before the copy was written; a cloud VM can be rebuilt with a
# fresh home folder. Session Guard therefore finds a conversation by its assistant message ids: in this machine's
# records (last and first ids) and in the project's store (first ids, Claude Code ids), which travels with the repo.
STOP_KINDS = ("context", "finish", "break")


def adopt_store_session(rec, data, sid, fork=False):
    srec = data["sessions"][sid]
    rec["long"] = rec.get("long") or bool(srec.get("long"))
    if fork:
        rec["sg_id"], rec["born"] = core.new_session_id(), core.now()
        return
    rec["sg_id"] = sid
    rec["born"] = rec.get("born") or core.to_time(srec.get("started")) or core.now()
    stopped = (srec.get("stopped") or {}).get("reason") or ""
    kind = stopped.split(":")[0]
    if kind in STOP_KINDS and kind not in rec["locks"]:
        sessions.lock(rec, kind, stopped)


def save_identity(root, rec, reading, long_mode=False):
    """Once: the conversation's first message ids (and long mode) go into the project's store."""
    first = reading.first_ids or reading.ids
    need_ids = bool(first) and not rec.get("id_saved")
    if not (need_ids or long_mode) or not root or not rec.get("sg_id") or not store.is_project(root):
        return
    try:
        store.mutate(root, lambda d: store.note_identity(d, rec["sg_id"], first if need_ids else (), long_mode),
                     render=False)
        if need_ids:
            rec["id_saved"] = True
    except (store.Refused, core.Busy):
        pass


def relink(root, rec, reading):
    """A record made without a match (the first hook ran on an unfinished copy) looks again while the transcript
    shows history older than the record: in this machine's records, then in the project's store. Returns the note
    for Claude when it found the conversation's earlier session, else None."""
    if not rec.get("unlinked"):
        return None
    if core.now() - rec["unlinked"] > sessions.LINK_WINDOW:
        rec["unlinked"] = 0
        return None
    if reading.oldest is None or reading.oldest >= (rec.get("born") or core.now()) - 120:
        return None
    ids, fresh, fork = reading.ids + reading.first_ids, rec["sg_id"], rec.get("source") == "fork"
    born = rec.get("born") or core.now()
    old = sessions.find(rec["claude_id"], ids, not_sg=fresh, before=born)
    data = store.load(root) if root and store.is_project(root) else None
    target = old.get("sg_id") if old else store.find_by_messages(data, None, ids, not_sg=fresh, before=core.iso(born))
    if not target or target == fresh:
        return None
    rec["unlinked"] = 0
    if old:
        rec["long"] = rec["long"] or bool(old.get("long"))
        rec["notified"] = list(dict.fromkeys((old.get("notified") or []) + [
            n for n in rec["notified"] if n not in measure.NOTIFY_ONCE]))
        rec["locks"] = dict(old.get("locks") or {}, **{k: v for k, v in rec["locks"].items() if k == "quota"})
        if not fork:
            rec["sg_id"], rec["born"] = target, old.get("born") or rec["born"]
    elif data:
        rec["locks"] = {k: v for k, v in rec["locks"].items() if k == "quota"}
        adopt_store_session(rec, data, target, fork=fork)
        if fork:
            rec["sg_id"] = fresh
    if data:
        try:
            store.mutate(root, lambda d: store.link(d, target, fresh) if fork else store.same_conversation(
                d, fresh, target, rec["claude_id"], core.place()), render=not fork)
        except (store.Refused, core.Busy):
            pass
    if not fork and rec.get("env_file"):
        try:
            core.append_text(rec["env_file"], "export SG_SESSION_ID=%s\n" % target)
        except OSError:
            pass
    if fork:
        return ("[Session Guard 2.0] FORK FOUND: this conversation is a copy of session %s (found from its messages). "
                "It keeps its own SESSION-ID %s with the same mode (%s); the tasks stay with %s." % (
                    target, fresh, "LONG" if rec["long"] else "normal", target))
    return ("[Session Guard 2.0] SAME CONVERSATION: Claude Code gave this conversation a new id; Session Guard found "
            "it from its messages. Your SESSION-ID is %s again (mode %s, its tasks and notices kept). %s still works "
            "as an alias of it." % (target, "LONG" if rec["long"] else "normal", fresh))


# --- session start ---------------------------------------------------------------------
def write_env(rec, root):
    p = os.environ.get("CLAUDE_ENV_FILE")
    if not p:
        return
    try:
        core.append_text(p, "export SG_SESSION_ID=%s\nexport SG_PROJECT_ROOT='%s'\n" % (
            rec["sg_id"], (root or "").replace("'", "")))
    except OSError:
        pass


def legacy_check(rec, data):
    if data and rec.get("sg_id") in data.get("sessions", {}):
        return False  # registered in the project after its migration (a legacy session is never registered)
    if data and data.get("migrated_at") and rec.get("born") is not None and rec["born"] < data["migrated_at"] \
            and rec["sg_id"] not in data.get("migration_sessions", []):
        sessions.lock(rec, "legacy", "conversation began before migration (%s)" % core.iso(data["migrated_at"]))
        return True
    return False


def home_check(rec, data):
    if data and data.get("home") and data["home"] != core.place():
        sessions.lock(rec, "home", "project lives in the %s" % data["home"], home=data["home"])
        return True
    return False


def lock_text(rec, data=None):
    kind = sessions.closed(rec)
    if kind == "handed":
        return msg("closed_handed", by=rec["locks"]["handed"].get("by", "another session"))
    if kind == "home":
        return msg("closed_home", home=rec["locks"]["home"].get("home", "other place"))
    if kind in ("context", "finish", "break", "legacy"):
        return msg("closed_" + kind)
    return None


def task_lines(data, sid):
    """Session start order (user decision 2026-11-05): STATUS only; first the work handed off and not taken over yet
    (propose finishing one); only when there is none and new work is wanted the backlog, then the ideas."""
    mine = store.owned_open(data, sid)
    out = ["Your tasks: " + (", ".join("%s (%s)" % (t["id"], t["status"]) for t in mine) or "none")]
    can = sorted(store.claimable(data, sid), key=lambda t: (t["kind"] != "urgent", t["priority"], store.sort_key(t["id"])))
    if len(can) > 1:
        out.append("SEVERAL TASKS CAN BE TAKEN OVER (%s). Unless the user's message is about one of them, propose "
                   "first to finish one of these (recommend %s: urgent and higher priority first; read it with `sg task "
                   "show <id>`); the user chooses, do not claim before that." % (", ".join(
                       "%s%s" % (t["id"], " hand-off" if t["handoff"] else " orphaned/free") for t in can), can[0]["id"]))
    elif can:
        t = can[0]
        out.append("One task can be taken over: %s \"%s\"%s. If the user's message is about it, read it (`sg task show "
                   "%s`) and claim it; otherwise propose first to finish it, and ask." % (
                       t["id"], t["title"], " (hand-off waiting)" if t["handoff"] else "", t["id"]))
    elif not mine:
        out.append("No task waits to be taken over. New work only when the user wants it, in this order: a queued "
                   "task (docs/TASK_BACKLOG.md, %d queued; `sg task start <id>`), else an idea (docs/IDEA_TASKS.md, %d): "
                   "design it with the user by questions, put it in the queue (`sg idea promote <id> --queue "
                   "--approval \"...\"`), then start it. Read those files only then; STATUS is enough otherwise." % (
                       store.queue_count(data), store.idea_count(data)))
    return out


def side_branch_lines(root, data):
    """Cloud (or a project that uses side branches): hand-offs whose work and record are on a side branch this
    checkout does not have yet. Their tasks are not in this checkout's store until the branch is merged."""
    uses = core.in_cloud() or any((t.get("work") or {}).get("mode") == "side-branch" for t in data["tasks"].values())
    if not uses or not gitops.is_repo(root):
        return []
    found = gitops.remote_side_branches(root)
    if not found:
        return []
    names = ", ".join(b for b, _ in found[:10]) + (" (+%d more)" % (len(found) - 10) if len(found) > 10 else "")
    return ["HAND-OFF BRANCHES not in this checkout: %s. Each holds a task's unfinished work and its hand-off. If the "
            "user's message is about one, run `sg handoff adopt <branch>` (merges it here), then `sg task show` / "
            "`sg task claim`; %s" % (names, "with several, ask the user which one." if len(found) > 1 else
                                     "otherwise ask.")]


def rule_review(root, data, rec):
    """New or changed CLAUDE.md rule lines (compared with the reviewed list in the store): one review request per
    change and session."""
    new = rules.new_rule_lines(root, data)
    if not new:
        return None
    mark = "rules:%d:%s" % (len(new), new[-1][:40])
    if mark in rec["notified"]:
        return None
    rec["notified"].append(mark)
    shown = "\n".join("  - " + l for l in new[:15]) + ("\n  (+%d more)" % (len(new) - 15) if len(new) > 15 else "")
    return rules.RULE_REVIEW % {"n": len(new), "shown": shown, "sg": sg_cmd()}


def start_context(root, rec, data, source):
    parts = [rules.RULES]
    if PROJECT_MODE:
        parts.append(rules.GENERAL_RULES)  # no global CLAUDE.md in a cloud session
    else:
        res = rules.ensure_global_block()
        rules.remove_v1_block()  # a 1.x session started before the update may have written it back
        if res == "broken":
            parts.append("WARNING: the session-guard2 markers in ~/.claude/CLAUDE.md are damaged; tell the user "
                         "(skill setup repairs it).")
    head = "[Session Guard 2.0] SESSION-ID %s (%s, %s) · command: sg = %s" % (
        rec["sg_id"], core.place(), "resumed" if source == "resume" else "new", sg_cmd())
    if data:
        head += " · PROJECT-ID %s (%s)" % (data["project_id"], root)
        idle = idle_line(data, rec["sg_id"])
        parts.append("\n".join([head] + task_lines(data, rec["sg_id"]) + side_branch_lines(root, data) +
                               ([idle] if idle else [])))
        review = rule_review(root, data, rec)
        if review:
            parts.append(review)
        status = core.exists_ci(root, "docs/STATUS.md")
        if status:
            parts.append("docs/STATUS.md (already loaded, do not read again):\n\n" + (core.read_text(status) or "")[:8000])
    else:
        parts.append(head)
        if root:
            parts.append("[Session Guard 2.0] MIGRATION PENDING for %s: thresholds, breaks and quota are guarded "
                         "here; PROJECT-ID, tasks and hand-offs are not active until this project is migrated (from "
                         "session-guard-executive, skill setup \"Migration\"). Do not create 2.0 files here yourself."
                         % root)
        else:
            parts.append("Not in a project folder (home, drive root, ~/.claude or a system folder): no project files.")
    loc = messages.locale()
    miss = messages.missing(loc)
    if miss and len(miss) < len(messages.TRANSLATABLE):
        parts.append("Session Guard notices without a translation for %s: %s. Translate them now as skill setup "
                     "(\"Notices\") says, without mentioning it." % (loc, ", ".join(miss)))
    return "\n\n".join(parts)


def on_session_start(inp):
    sessions.clean()
    cid, source = inp.get("session_id"), inp.get("source") or "startup"
    root = core.project_root(inp)
    act = activation(root)
    if act != "active":
        return emit(inactive(act, "SessionStart"))
    reading = measure.read_transcript(inp.get("transcript_path"))
    if core.in_cloud() and root and gitops.is_repo(root) and not os.path.exists(sessions.path_for(cid)):
        sessions.restore(root)  # a rebuilt VM: the records kept on the sg-state branch come back first
    rec, known = sessions.load(cid, reading.ids + reading.first_ids)
    forked_from = None
    if known and source == "fork" and rec.get("continues") and rec["continues"] != cid:
        # /fork, /branch, --fork-session: both conversations may go on, so the fork is its own session. It keeps the
        # mode (long mode included), the notices and the locks: it carries the same context.
        forked_from, rec["sg_id"], rec["born"] = rec["sg_id"], core.new_session_id(), core.now()
    data = store.load(root) if root else None
    if not known and data:
        # Another machine (a cloud VM rebuilt) or a lost record: the project's own store finds the session from
        # the conversation's messages.
        found = store.find_by_messages(data, cid, reading.ids + reading.first_ids)
        if found:
            adopt_store_session(rec, data, found, fork=source == "fork")
            if source == "fork":
                forked_from = found
            known = True
    if not known:
        rec["sg_id"] = core.new_session_id()
        rec["born"] = core.now() if source in ("startup", "clear", "fork") else (reading.first_at or 0)
        rec["unlinked"], rec["source"] = core.now(), source  # keeps looking for its earlier id (relink)
    rec["env_file"] = os.environ.get("CLAUDE_ENV_FILE") or rec.get("env_file")
    if source == "compact":
        # Claude Code summarized the conversation: the context is small again, so the one-time notices fire again
        # as it grows. Hand-off steps stay recorded (a hand-off is not written twice).
        rec["notified"] = [n for n in rec["notified"] if n not in measure.NOTIFY_ONCE]
    rec.update(self=ENTRY, root=root, place=core.place())
    if data:
        rec["project_id"] = data["project_id"]
    old = sessions.v1_lock(cid)
    if old and old not in rec["locks"]:
        sessions.lock(rec, old, "locked by Session Guard 1.x")
    legacy_check(rec, data)
    home_check(rec, data)
    hit = None if PROJECT_MODE else measure.quota_hit()
    if hit:
        quota_lock(rec, hit)
    sessions.save(rec, reading.ids, reading.first_ids or reading.ids)
    if sessions.closed(rec) or hit:
        text = lock_text(rec) or quota_text(rec["locks"]["quota"])
        return emit(ctx("SessionStart", "[Session Guard 2.0] This session is locked: %s Do no work; every prompt is "
                                        "blocked." % text, text))
    write_env(rec, root)
    if data:
        try:
            store.mutate(root, lambda d: store.register_session(d, rec["sg_id"], cid, core.place(), parent=forked_from),
                         render=False)
            data = store.load(root)
            if not core.exists_ci(root, "docs/IDEA_TASKS.md"):  # ideas used to live in TASK_BACKLOG.md
                with core.FileLock(store.lock_path(root)):
                    render.render_backlog(root, data)
                    render.render_ideas(root, data)
                    render.render_status(root, data)
        except (store.Refused, core.Busy):
            pass
    elif root:
        try:
            migrate.note_seen(root)
        except (OSError, core.Busy):
            pass
    out = start_context(root, rec, data, source)
    if source == "compact":
        out += ("\n\n[Session Guard 2.0] COMPACTED: Claude Code summarized this conversation. What is not in the "
                "files may be lost: before going on, read your open tasks with `%s task show <id>` (the rules and "
                "STATUS above are current). The context is measured again from now on." % sg_cmd())
    if forked_from:
        out += ("\n\n[Session Guard 2.0] FORK of session %s: this conversation is a new session (%s) with the same "
                "context and mode. It owns no task yet: the tasks stay with %s unless the user says otherwise." % (
                    forked_from, rec["sg_id"], forked_from))
    merged = []
    if data and gitops.is_repo(root) and (core.in_cloud() or any(
            (t.get("work") or {}).get("mode") == "side-branch" for t in data["tasks"].values())):
        merged = gitops.merged_side_branches(root)
        if merged:
            out += ("\n\n[Session Guard 2.0] MERGED SIDE BRANCHES (the user was told): %s. After you merge a pull "
                    "request or a side branch yourself, tell the user the same in one line." % ", ".join(merged))
    mig = migrate.load()
    if mig["phase"] == "paused":
        out += ("\n\n[Session Guard 2.0] INSTALLATION PAUSED (%s). Only \"continue installation\" in %s continues it; "
                "until then every prompt is blocked." % ((mig.get("paused") or {}).get("reason"), EXECUTIVE))
    user = msg("migration_pending") if root and not data and source == "startup" else None
    if merged:
        user = msg("side_branches_merged", names=", ".join(merged[:10]))
    return emit(ctx("SessionStart", out, user))


# --- quota helpers (v1.4) -----------------------------------------------------------------
def quota_lock(rec, hit):
    name, reading = hit
    sessions.lock(rec, "quota", "quota", window=name, percent=round(reading["used_percent"]),
                  resets_at=reading["resets_at"])


def quota_text(info, name="quota_block"):
    window = info.get("window") if info.get("window") in dict(measure.WINDOWS) else "five_hour"
    return msg(name, percent=info.get("percent", 0), window=msg("window_" + window)[len("Session Guard: "):],
               time=measure.clock(info.get("resets_at", 0)))


# --- prompts ----------------------------------------------------------------------------------
def block(text):
    return emit({"decision": "block", "reason": text})


def handed_lock(rec, data):
    srec = (data or {}).get("sessions", {}).get(rec.get("sg_id") or "")
    if srec and srec["state"] == "locked" and "handed" not in rec["locks"]:
        found = re.search(r" to (S-\d{8}-[0-9a-f]{6}(?:-[BI]\d+)*)", srec.get("lock_reason") or "")
        sessions.lock(rec, "handed", srec.get("lock_reason") or "handed over",
                      by=found.group(1) if found else "another session")
        return True
    return False


def on_prompt(inp):
    root = core.project_root(inp)
    act = activation(root)
    if act in ("v1", "executive"):
        return 0
    cid = inp.get("session_id")
    raw_prompt = inp.get("prompt") or ""
    prompt = measure.normalize(raw_prompt)
    reading = measure.read_transcript(inp.get("transcript_path"))
    rec, known = sessions.load(cid, reading.ids + reading.first_ids)
    if not known or (rec.get("self") and rec["self"] != ENTRY and not PROJECT_MODE):
        if not PROJECT_MODE and act == "active":
            return block(msg("restart_plugin"))
        rec["sg_id"] = rec.get("sg_id") or core.new_session_id()
        rec["born"] = rec.get("born") or core.now()
        rec["self"] = ENTRY
    relinked = relink(root, rec, reading)
    core.write_text(break_token_path(cid), "prompt")  # the user is back: a waiting break watch stops
    rec["break_wake"] = 0
    rec["last_context"], rec["last_reply"] = reading.context, reading.last_reply
    if act == "setup":
        sessions.save(rec, reading.ids, reading.first_ids or reading.ids)
        return emit(inactive(act, "UserPromptSubmit"))
    mig = migrate.load()
    head = []
    if mig["phase"] == "paused":
        if measure.has_command(measure.CONTINUE_INSTALL, prompt) and is_executive(root):
            info = migrate.resume(cid)
            head.append("[Session Guard 2.0] CONTINUE INSTALLATION. Checkpoint:\n%s\nPaused because: %s. Continue with "
                        "skill setup \"Migration\" from the first project not verified; do nothing else." % (
                            migrate.summary(), (info or {}).get("reason")))
        else:
            sessions.save(rec, reading.ids, reading.first_ids or reading.ids)
            return block(msg("migration_paused"))
    data = store.load(root) if root else None
    if closed_now(rec, data):
        sessions.save(rec, reading.ids, reading.first_ids or reading.ids)
        return block(lock_text(rec))
    quota = rec["locks"].get("quota")
    if quota and measure.quota_cleared(quota):
        del rec["locks"]["quota"]
        quota = None
    if quota:
        sessions.save(rec, reading.ids, reading.first_ids or reading.ids)
        return block(quota_text(quota))

    notes, user = ([relinked] if relinked else []), []
    refused = None
    finish = measure.has_command(measure.FINISH, prompt)
    if finish and measure.handed(rec):
        refused, finish = msg("finish_refused", k=measure.handed(rec)), False
    # The break is decided by the mode the session had before this message: "start long mode" after a long break
    # does not skip the break rule (bug found on v1.4, 2026-11-05).
    br = None if finish else measure.break_decision(reading, rec["long"], finish)
    was_long, long_skipped = rec["long"], False
    if measure.has_command(measure.LONG, prompt) and not rec["long"]:
        if measure.handed(rec):
            refused = msg("long_refused")
        elif br and br[0] == "lock":
            long_skipped = True
        else:
            rec["long"] = True
            save_identity(root, rec, reading, long_mode=True)
            notes.append("LONG MODE is on for this session (cannot be switched off). Confirm it to the user in one line.")
    long_mode = rec["long"]
    gap = time.time() - reading.last_reply if reading.last_reply else None
    line = "[Session Guard] Context: %s (last reply). Mode: %s. Since last reply: %s.%s" % (
        measure.kfmt(reading.context), "LONG" if long_mode else "normal",
        "unknown" if gap is None else "%d min" % (gap // 60),
        " Background jobs still running: %d (break rule off)." % reading.pending if reading.pending else "")
    limit = measure.LONG_LIMIT if long_mode else measure.NORMAL_LIMIT
    if reading.context is not None and reading.context >= limit and not finish:
        stop(root, rec, "context", "context %s" % measure.kfmt(reading.context))
        sessions.save(rec, reading.ids, reading.first_ids or reading.ids)
        return block(msg("hard_limit", k=measure.kfmt(reading.context)))
    if refused:
        sessions.save(rec, reading.ids, reading.first_ids or reading.ids)
        return block(refused)

    child = child_session(root, rec, raw_prompt) if data else None
    if child:
        notes.append(child)
        data = store.load(root)
    sid = rec["sg_id"]
    if data and not finish:
        r = parent_note(data, rec)
        if r:
            notes.append(r)
    if finish:
        stop(root, rec, "finish", "finish session")
        notes.append('FINISH SESSION. Do nothing else the message asks. Only skill session-guard "Finish session": %s.'
                     '%s The session is locked after this reply.' % (handoff_todo(data, sid), measure_todo(root)))
        user.append(msg("finishing"))
    else:
        if br and br[0] == "lock":
            stop(root, rec, "break", "break over 1 hour, context %s" % measure.kfmt(reading.context))
            if data:
                try:
                    store.mutate(root, lambda d: store.record_user_command(d, sid, raw_prompt))
                except (store.Refused, core.Busy):
                    pass
            notes.append("BREAK RULE (gap %d min, context %s > 175k). Do NOT run the user's command and do no reading or "
                         "research for it. Only skill session-guard \"Break hand-off\": %s. Session Guard stored the "
                         "command word for word. Tell the user to continue in a new session. The session is locked "
                         "after this reply.%s" % (br[1] // 60, measure.kfmt(reading.context), handoff_todo(data, sid),
                                                  " LONG MODE was NOT turned on: the break rule comes first; tell the "
                                                  "user it can be started in the new session." if long_skipped else ""))
            user.append(msg("break_lock", k=measure.kfmt(reading.context)))
        elif br:
            notes.append("BREAK REMINDER: gap over 1 hour (%d min), context %s <= 175k. Remind the user in one line, "
                         "then handle the command normally." % (br[1] // 60, measure.kfmt(reading.context)))
            user.append(msg("break_remind", k=measure.kfmt(reading.context)))
    probe = False
    if rec.get("break_probe"):
        measure.close_probe(rec)  # the turn after a measured one: what was read is written
    if not finish and not PROJECT_MODE and gap is not None and gap > measure.BREAK_SECONDS and not reading.pending \
            and measure.probe_on():
        measure.start_probe(rec, reading, gap, was_long, br[0] if br else None)
        notes.append(measure.PROBE_READ)
        probe = True
    if reading.stale and not sessions.closed(rec):
        notes.append(STALE_JOBS % reading.stale)
        if "stale24" not in rec["notified"]:
            rec["notified"].append("stale24")
            user.append(msg("stale"))
    if not sessions.closed(rec):
        step = measure.step_for(reading.context, long_mode)
        if step:
            if step[1] not in measure.NOTIFY_ONCE or step[1] not in rec["notified"]:
                notes.append(step[2] + (measure.NOTICE_FORM % (step[0] // core.K) if step[1] not in rec["notified"]
                                        else "") + (" " + handoff_todo(data, sid) if step[1] in measure.HANDOFF_STEPS
                                                    and data else "")
                             + (measure_todo(root) if step[1] in measure.MEASURE_STEPS
                                and step[1] not in rec["notified"] else ""))
            if step[1] not in rec["notified"]:
                rec["notified"].append(step[1])
                user.append(msg(step[1]))
        hit = None if PROJECT_MODE else measure.quota_hit()
        if hit:
            quota_lock(rec, hit)
            notes.append(measure.QUOTA_STOP % (dict(measure.WINDOWS)[hit[0]], hit[1]["used_percent"],
                                               measure.clock(hit[1]["resets_at"])))
            user.append(quota_text(rec["locks"]["quota"], "quota_stop"))
        elif measure.needs_reading(rec, False) and not probe:
            notes.append(measure.READ_USAGE)
    if data:
        restored = restore_status(root)
        if restored:
            notes.append(restored)
            user.append(msg("status_restored"))
    notes += budget_notes(cid, reading, rec)
    sessions.save(rec, reading.ids, reading.first_ids or reading.ids)
    if not notes and not head:
        return 0
    data_out = ctx("UserPromptSubmit", "\n\n".join(head + (["\n".join([line] + notes)] if notes else [])))
    if user:
        data_out["systemMessage"] = " ".join(user[:1] + [u[len("Session Guard: "):] for u in user[1:]])
    return emit(data_out)


def closed_now(rec, data):
    """Permanent locks, including those decided elsewhere: the store says this session handed everything over,
    or the project was migrated after this conversation began."""
    if sessions.closed(rec):
        return True
    return handed_lock(rec, data) or with_parent_lock(rec, data) or legacy_check(rec, data) or \
        home_check(rec, data)


def handoff_todo(data, sid):
    if not data:
        return "hand-off as the skill says (this project is not on 2.0 yet: no task ids)"
    missing = store.missing_handoffs(data, sid)
    mine = [t["id"] for t in store.owned_open(data, sid)]
    kids = store.children_of(data, sid)
    first = ("FIRST `%s handoff children`: your child session agents (%s) are told, and the bound ones write their "
             "hand-offs before yours (it prints the message to send them); " % (sg_cmd(), ", ".join(c for c, _ in kids))
             if kids else "")
    if not mine:
        return first + "you own no open task: no hand-off needed"
    return first + ("tasks you own: %s; hand-off still missing for: %s. First bring each task's work to a hand-off-ready "
            "point (finish the small step in progress, leave nothing half-written), then `%s handoff write <id> "
            "--technical ...`%s, then ONE session note (`%s handoff note --text \"<overall assessment, 2-3 sentences>\"`: "
            "signature, date and quota are added), then `%s handoff publish`" % (
                ", ".join(mine), ", ".join(missing) or "none", sg_cmd(),
                " (cloud: it commits the task's work for its side branch; with several tasks add --paths)"
                if core.in_cloud() else "", sg_cmd(), sg_cmd()))


def restore_status(root):
    """A hand edit of the generated STATUS block is undone from the store (the text outside the block is kept)."""
    if render.block_intact(root):
        return None
    data = store.load(root)
    if not data:
        return None
    try:
        with core.FileLock(store.lock_path(root)):
            render.render_status(root, store.load(root))
    except core.Busy:
        return None
    return ("[Session Guard 2.0] The task block of docs/STATUS.md was edited by hand; it was rebuilt from the task "
            "store. Change tasks only with `sg task ...`; free text goes under \"## Notes\".")


def absorb_notes(root, sid, name="STATUS.md", heading="## notes"):
    """Bullets written by hand under "## Notes" of STATUS.md or "## Add an idea" of IDEA_TASKS.md become ideas
    with temporary ids (IDEA-xxx), checked against the existing work; nothing waits without an id. A bullet the
    idea limit or the duplicate check refuses stays, with the reason."""
    path = render.doc(root, name)
    text = core.read_text(path)
    if not text or not sid:
        return None
    if name == "STATUS.md":
        before, body, after = render.split_status(text)
        if body is None:
            return None
    else:
        after = text
    found, inside = [], False
    for line in after.splitlines():
        if line.startswith("#"):
            inside = line.strip().lower().startswith(heading)
        elif inside and render.ITEM.match(line):
            item = re.sub(r"^\s*(?:[-*+]|\d+[.)])\s+", "", line).strip()
            if item.lower().strip(".") not in ("none", "yok"):
                found.append((line, item))
    if not found:
        return None
    added, refused, gone = [], [], set()
    for line, item in found:
        try:
            added.append(store.mutate(root, lambda d: store.add_idea(d, sid, item, source="status-notes"),
                                      render=False))
            gone.add(line)
        except (store.Refused, core.Busy) as e:
            refused.append("\"%s\": %s" % (item[:60], e))
    if gone:
        keep = [l for l in after.splitlines() if l not in gone]
        core.write_text(path, text[:len(text) - len(after)] + "\n".join(keep) + ("\n" if keep else ""))
        try:
            with core.FileLock(store.lock_path(root)):
                data = store.load(root)
                render.render_status(root, data)
                render.render_ideas(root, data)
        except core.Busy:
            pass
    return "[Session Guard 2.0] %s %s: %s%s" % (name, heading.strip("# ").title(),
        "became ideas %s (temporary ids; `sg idea promote` makes a task). " % ", ".join(added) if added else "",
        ("Refused, still in Notes: " + "; ".join(refused)) if refused else "")


def tidy_status(root):
    """Empty lines and empty bullets an edit left in STATUS.md are removed (the generated block is untouched)."""
    path = render.doc(root, "STATUS.md")
    before = core.read_text(path)
    try:
        with core.FileLock(store.lock_path(root)):
            render.render_status(root, store.load(root))
    except core.Busy:
        return None
    if core.read_text(path) == before:
        return None
    return ("[Session Guard 2.0] docs/STATUS.md: empty lines or empty bullets left by an edit were removed. Edit "
            "STATUS by section (its heading), never by line number.")


def budget_notes(cid, reading, rec):
    mig = migrate.load()
    if cid not in mig["sessions"] or mig["phase"] != "running":
        return []
    used, limit = migrate.measure_session(cid, reading)
    if used >= limit:
        migrate.pause("token budget used (%dk of %dk)" % (used // 1000, limit // 1000))
        return ["[Session Guard 2.0] INSTALLATION BUDGET USED (%dk of %dk). The installation is paused now. Record "
                "where you are (`%s migrate status`), tell the user to type \"continue installation\" in a new %s "
                "session, and do nothing else." % (used // 1000, limit // 1000, sg_cmd(), EXECUTIVE)]
    if used >= migrate.SAFE_AT * limit and "budget90" not in rec["notified"]:
        rec["notified"].append("budget90")
        return ["[Session Guard 2.0] INSTALLATION BUDGET %dk of %dk (90%%). Finish the current project step to a safe "
                "point (survey, apply or verify done), then run `%s migrate pause --reason budget` and tell the user to "
                "continue with \"continue installation\" in a new %s session." % (
                    used // 1000, limit // 1000, sg_cmd(), EXECUTIVE)]
    return []


# --- tools -------------------------------------------------------------------------------------
EDIT_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit")


def tool_path(inp):
    args = inp.get("tool_input") or {}
    return str(args.get("file_path") or args.get("notebook_path") or "")


def deny(reason):
    return emit({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                        "permissionDecisionReason": reason}})


SG_MAIN_WORK = re.compile(r"session_guard\.py\S*\s.*\b(?:task\s+(?:new|start|claim|reopen)|idea\s+promote)\b")
SG_SESSION_ARG = re.compile(r"session_guard\.py\S*\s.*--session[=\s]+[\"']?(S-\d{8}-[0-9a-f]{6}(?:-[BI]\d+)*)")


def foreign_session(inp, cmd):
    """An `sg` command run with another session's id (`--session`): a session acts only as itself, so ownership
    cannot be dodged by borrowing an id. Its first id (the alias of a child id) counts as its own."""
    m = SG_SESSION_ARG.search(cmd)
    if not m:
        return None
    rec, known = sessions.load(inp.get("session_id"))
    own = rec.get("sg_id") if known else None
    if not own:
        return None
    root = core.project_root(inp)
    data = store.load(root) if root else None
    if store.resolve(data, m.group(1)) == store.resolve(data, own):
        return None
    return deny("Session Guard 2.0: --session %s is not this session (%s). A session acts only as itself; work "
                "owned by another session is taken over only with `sg task claim` (and the user's yes while that "
                "session is active)." % (m.group(1), own))


def subagent_check(inp):
    """A subagent works only on a subtask of a task its session owns: the session cannot hand it a new, independent
    task (user decision 2026-11-04). web-fetch only reads a page (it stands in for a tool) and needs no task."""
    if inp.get("tool_name") == "Bash":
        cmd = str((inp.get("tool_input") or {}).get("command") or "")
        other = foreign_session(inp, cmd)
        if other is not None:
            return other
        if subagent(inp) and SG_MAIN_WORK.search(cmd) and not (" new" in cmd and "--parent" in cmd):
            return deny("Session Guard 2.0: a subagent opens, starts or claims no main task; it works only on the "
                        "subtask it was given. Report back to the session that started you.")
        return 0
    args, root = inp.get("tool_input") or {}, core.project_root(inp)
    if args.get("subagent_type") == "web-fetch" or not (root and store.is_project(root)):
        return 0
    rec, known = sessions.load(inp.get("session_id"))
    data = store.load(root)
    sid = rec.get("sg_id") if known else None
    mine = {t["id"] for t in store.owned_open(data, sid)} if sid else set()
    named = set(core.TASK_ID_ANY.findall(str(args.get("prompt") or "")))
    subtasks = {t for t in named & mine if data["tasks"][t]["parent"]}
    if subtasks:
        return 0
    return deny("Session Guard 2.0: give a subagent only a subtask of a task you own. Open it first (`%s task new "
                "--parent <your TASK-ID> --title \"...\"`), then name the subtask id (TASK-xxx-n) in the subagent's "
                "prompt. A new, independent task never goes to a subagent: it needs the user's approval and is yours "
                "(or a queued task / an idea). Subtasks do not count toward the limits." % sg_cmd())


def on_pre_tool(inp):
    """The task store is changed only through `sg` (lock, ownership, revision): direct edits are refused. Subagents
    get only subtasks."""
    if inp.get("tool_name") in ("Agent", "Task", "Bash"):
        return subagent_check(inp)
    if inp.get("tool_name") not in EDIT_TOOLS:
        return 0
    p = tool_path(inp).replace("\\", "/")
    if STORE_DIR_MARK in p:
        return emit({"hookSpecificOutput": {
            "hookEventName": "PreToolUse", "permissionDecision": "deny",
            "permissionDecisionReason": "Session Guard 2.0: the task store is changed only with `%s task ...` / "
                                        "`handoff ...` (ownership and lock checks)." % sg_cmd()}})
    return 0


def on_tool(inp):
    root = core.project_root(inp)
    if activation(root) != "active":
        return 0
    if inp.get("tool_name") == measure.USAGE_TOOL and not PROJECT_MODE:
        measure.take_usage(inp.get("tool_response"))
    cid = inp.get("session_id")
    reading = measure.read_transcript(inp.get("transcript_path"))
    rec, known = sessions.load(cid, reading.ids + reading.first_ids)
    if known and inp.get("tool_name") == measure.USAGE_TOOL and rec.get("break_probe") and not subagent(inp):
        measure.probe_reading(rec)  # before the lock check: a break hand-off turn is measured too
        sessions.save(rec, reading.ids, reading.first_ids or reading.ids)
    if not known:
        return 0
    relinked = relink(root, rec, reading)
    if relinked:
        sessions.save(rec, reading.ids, reading.first_ids or reading.ids)
        return emit({"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": relinked}})
    save_identity(root, rec, reading)
    rec["last_context"], rec["last_reply"] = reading.context, reading.last_reply or rec.get("last_reply")
    if rec.get("break_wake") and not sessions.closed(rec) and not subagent(inp):
        rec["break_wake"] = 0
        stop(root, rec, "break", "break hand-off at %d min (break watch), context %s" % (
            BREAK_WAKE // 60, measure.kfmt(reading.context)))
        sessions.save(rec, reading.ids, reading.first_ids or reading.ids)
        return 0
    if sessions.closed(rec):
        return 0
    quota = rec["locks"].get("quota")
    if quota:
        if measure.quota_cleared(quota):
            del rec["locks"]["quota"]
            sessions.save(rec, reading.ids, reading.first_ids or reading.ids)
        elif time.time() - quota.get("at", 0) > measure.QUOTA_GRACE:
            text = quota_text(quota, "quota_stopped")
            return emit({"continue": False, "stopReason": text, "systemMessage": text})
        else:
            return 0
    if subagent(inp):
        return 0
    extra = []
    edited = os.path.abspath(tool_path(inp)) if inp.get("tool_name") in EDIT_TOOLS and tool_path(inp) else ""
    if edited and os.path.basename(edited).lower() == "status.md" and root and store.is_project(root):
        for r in (restore_status(root), absorb_notes(root, rec.get("sg_id")), tidy_status(root)):
            if r:
                extra.append(r)
    name = os.path.basename(edited).lower() if edited else ""
    if name in ("task_backlog.md", "idea_tasks.md") and root and store.is_project(root):
        r = absorb_notes(root, rec.get("sg_id"), "IDEA_TASKS.md", render.ADD_IDEA.lower()) \
            if name == "idea_tasks.md" else None
        before = core.read_text(edited)
        with core.FileLock(store.lock_path(root)):
            data = store.load(root)
            render.render_backlog(root, data) if name == "task_backlog.md" else render.render_ideas(root, data)
        if r:
            extra.append(r)
        elif core.read_text(edited) != before:  # both files are generated: a hand edit is undone
            extra.append("[Session Guard 2.0] docs/%s is generated from the task store; the hand edit was undone. "
                         "Queued tasks: `sg task new --queue --approval`; ideas: `sg idea add` or a bullet under "
                         "\"%s\" in docs/IDEA_TASKS.md. Change entries by their id, never by line number." % (
                             "TASK_BACKLOG.md" if name == "task_backlog.md" else "IDEA_TASKS.md", render.ADD_IDEA))
    if edited and root and core.key(edited) == core.key(os.path.join(root, "CLAUDE.md")) and store.is_project(root):
        text = core.read_text(edited)
        if text and render.tidy(text) != text:
            core.write_text(edited, render.tidy(text))
            extra.append("[Session Guard 2.0] CLAUDE.md: empty lines or empty bullets left by an edit were removed. "
                         "Edit CLAUDE.md by section and rule id, never by line number.")
        r = rule_review(root, store.load(root), rec)
        if r:
            extra.append(r)
    if root and store.is_project(root):
        r = parent_note(store.load(root), rec)
        if r:
            extra.append(r)
    turn = "turn24:%s" % reading.prompt_at
    if reading.prompt_at and time.time() - reading.prompt_at > measure.PENDING_MAX and turn not in rec["notified"]:
        rec["notified"].append(turn)
        sessions.save(rec, reading.ids, reading.first_ids or reading.ids)
        return emit(ctx("PostToolUse", LONG_TURN, msg("stale")))
    step = measure.step_for(reading.context, rec["long"])
    if step and step[1] not in rec["notified"]:
        rec["notified"].append(step[1])
        data = ctx("PostToolUse", "[Session Guard] Context: %s (during work). %s%s" % (
            measure.kfmt(reading.context), step[2], measure.NOTICE_FORM % (step[0] // core.K)
            + (measure_todo(root) if step[1] in measure.MEASURE_STEPS else "")), msg(step[1]))
        if step[3]:
            stop(root, rec, "context", "context %s" % measure.kfmt(reading.context))
            data.update({"continue": False, "stopReason": msg(step[1])})
        sessions.save(rec, reading.ids, reading.first_ids or reading.ids)
        return emit(data)
    hit = None if PROJECT_MODE else measure.quota_hit()
    if hit:
        quota_lock(rec, hit)
        sessions.save(rec, reading.ids, reading.first_ids or reading.ids)
        return emit(ctx("PostToolUse", "[Session Guard] " + measure.QUOTA_STOP % (
            dict(measure.WINDOWS)[hit[0]], hit[1]["used_percent"], measure.clock(hit[1]["resets_at"])),
            quota_text(rec["locks"]["quota"], "quota_stop")))
    extra += budget_notes(cid, reading, rec)
    if measure.needs_reading(rec, True):
        extra.append(measure.READ_USAGE)
    sessions.save(rec, reading.ids, reading.first_ids or reading.ids)
    if extra:
        return emit(ctx("PostToolUse", "\n".join(extra)))
    return 0


# --- stop / end ----------------------------------------------------------------------------------
def on_stop(inp):
    """After "finish session", a break hand-off or the 300k / 800k hand-off: Claude is kept working (twice at most)
    while a task it owns has no hand-off yet or one not yet published, or, in the cloud, while work is not saved
    (a cloud checkout is gone with its session)."""
    root = core.project_root(inp)
    if activation(root) != "active" or subagent(inp):
        return 0
    rec, known = sessions.load(inp.get("session_id"))
    if not known or rec.get("stop_blocks", 0) >= 2 or not (
            {"finish", "break"} & set(rec["locks"]) or measure.handed(rec) or "parent_handoff" in rec["notified"]):
        return 0
    data = store.load(root) if root else None
    if not data:
        return 0
    sid = rec["sg_id"]
    missing = store.missing_handoffs(data, sid)
    unpublished = [t["id"] for t in store.owned_open(data, sid) if t["handoff"] and t["handoff"]["state"] == "prepared"]
    unsaved = gitops.changed_files(root) if core.in_cloud() and gitops.is_repo(root) and store.owned_open(data, sid) \
        else []
    if not missing and not unpublished and not unsaved:
        return 0
    rec["stop_blocks"] = rec.get("stop_blocks", 0) + 1
    sessions.save(rec)
    todo = []
    if unsaved:
        todo.append("save the unfinished work (%s) to its task: `%s handoff write <id> --technical ... --paths "
                    "<files>`" % (", ".join(unsaved[:8]), sg_cmd()))
    if missing:
        todo.append("write the hand-off for %s (`%s handoff write <id> --technical ...`)" % (", ".join(missing), sg_cmd()))
    todo.append("write your session note (`%s handoff note --text \"...\"`)" % sg_cmd())
    todo.append("publish the hand-offs (`%s handoff publish`)" % sg_cmd())
    return emit({"decision": "block", "reason": "Session Guard 2.0: before the session closes, %s." % "; then ".join(
        todo)})


def on_session_end(inp):
    root = core.project_root(inp)
    if activation(root) != "active":
        return 0
    rec, known = sessions.load(inp.get("session_id"))
    data = store.load(root) if root else None
    if known and data and rec.get("sg_id") in data["sessions"]:
        try:
            store.mutate(root, lambda d: store.end_session(d, rec["sg_id"], inp.get("reason")))
        except (store.Refused, core.Busy):
            pass
    return 0


def watch(inp):
    """24-hour watch (v1.4): an async hook started with every prompt; the newest replaces older ones."""
    cid = inp.get("session_id")
    token_path = os.path.join(core.state_dir(), "watch", core.safe_name(cid))
    token = "%f %d" % (time.time(), os.getpid())
    core.write_text(token_path, token)
    deadline = time.time() + WATCH_SECONDS
    while time.time() < deadline:
        time.sleep(max(0, min(WATCH_STEP, deadline - time.time())))
        if core.read_text(token_path) != token:
            return 0
    if activation(core.project_root(inp)) != "active":
        return 0
    reading = measure.read_transcript(inp.get("transcript_path"))
    rec, known = sessions.load(cid, reading.ids + reading.first_ids)
    if not known or rec["locks"] or not (reading.pending or reading.stale or reading.turn_open):
        return 0
    sys.stderr.write(WATCH_WAKE)
    return 2


def break_token_path(cid):
    return os.path.join(core.state_dir(), "breakwatch", core.safe_name(cid))


def break_watch(inp):
    """Break watch (user decision 2026-11-05): an async hook started when Claude ends a turn; a newer turn end or a
    prompt replaces it. 55 minutes after the last reply with no new message, in normal mode and over 175k, it wakes
    Claude (asyncRewake, exit 2) to hand off while the prompt cache still holds (it expires at one hour; the break
    rule would stop the next message anyway). The break lock is set only when Claude really starts (PostToolUse);
    if the wake never comes (app closed), the break rule acts at the next message as before."""
    cid = inp.get("session_id")
    if PROJECT_MODE or subagent(inp):
        return 0
    token_path = break_token_path(cid)
    token = "%f %d" % (time.time(), os.getpid())
    core.write_text(token_path, token)
    reading = measure.read_transcript(inp.get("transcript_path"))
    rec, known = sessions.load(cid, reading.ids + reading.first_ids)
    if not known or rec["long"] or sessions.closed(rec) or reading.last_reply is None:
        return 0
    wake_at = reading.last_reply + BREAK_WAKE
    while time.time() < wake_at:
        time.sleep(max(0, min(WATCH_STEP, wake_at - time.time())))
        if core.read_text(token_path) != token:
            return 0
    root = core.project_root(inp)
    if activation(root) != "active":
        return 0
    reading = measure.read_transcript(inp.get("transcript_path"))
    rec, known = sessions.load(cid, reading.ids + reading.first_ids)
    if not known or rec["long"] or rec["locks"] or reading.pending or reading.turn_open or reading.last_reply is None \
            or time.time() - reading.last_reply < BREAK_WAKE or (reading.context or 0) <= measure.BREAK_CONTEXT:
        return 0
    rec["break_wake"] = time.time()
    sessions.save(rec, reading.ids, reading.first_ids or reading.ids)
    data = store.load(root) if root else None
    sys.stderr.write(
        "[Session Guard 2.0] BREAK HAND-OFF NOW (%d min without a user message, context %s > 175k, normal mode): "
        "the prompt cache expires at one hour and the break rule would stop the next message, so hand off now "
        "while it is cheap. Do only skill session-guard \"Break hand-off\": %s. Start nothing else. The session is "
        "locked when you start; tell the user in one line that the work is handed off and to continue in a new "
        "session." % ((time.time() - reading.last_reply) // 60, measure.kfmt(reading.context),
                      handoff_todo(data, rec["sg_id"])))
    return 2


def idle_line(data, me):
    """2a (user decision 2026-11-05): at session start, other sessions of this project that own open tasks and have
    been idle over one hour, with what the break rule will do to them."""
    rows = []
    recs = {}
    for p in glob.glob(os.path.join(sessions.folder(), "*.json")):
        r = core.read_json(p)
        if isinstance(r, dict) and r.get("sg_id") and r.get("project_id") == data["project_id"]:
            if r["sg_id"] not in recs or (r.get("saved_at") or 0) > (recs[r["sg_id"]].get("saved_at") or 0):
                recs[r["sg_id"]] = r
    for sid, srec in sorted(data["sessions"].items()):
        if sid == me or srec.get("state") != "active":
            continue
        tasks = [t["id"] for t in store.owned_open(data, sid) if not t["handoff"]]
        if not tasks:
            continue
        r = recs.get(sid)
        if not r:
            rows.append("%s owns %s; no record on this machine (another place, or it ended without a hand-off)" % (
                sid, ", ".join(tasks)))
            continue
        last = r.get("last_reply") or r.get("saved_at")
        if not last or core.now() - last < measure.BREAK_SECONDS:
            continue
        ctxk = r.get("last_context")
        rule = "long mode: no break rule" if r.get("long") else (
            "break rule applies: its next message hands off and locks it" if (ctxk or 0) > measure.BREAK_CONTEXT
            else "break reminder only (context <= 175k)")
        if sessions.closed(sessions.fix(r, r.get("claude_id"))):
            rule = "locked without a hand-off"
        rows.append("%s owns %s; idle %d h %02d min, context %s, %s" % (
            sid, ", ".join(tasks), (core.now() - last) // 3600, (core.now() - last) % 3600 // 60,
            measure.kfmt(ctxk), rule))
    if not rows:
        return None
    return ("IDLE SESSIONS WITH OPEN TASKS: %s. Tell the user in one line per session (in their language). The work "
            "is not handed off yet: either the user opens that conversation and sends any message (it hands off by "
            "itself under the break rule, or goes on in long mode), or this session takes a task over with the "
            "user's yes (`%s task claim <id> --takeover \"<their words>\"`)." % ("; ".join(rows), sg_cmd()))


def dispatch(inp, args):
    event = inp.get("hook_event_name")
    if event == "SessionStart":
        return on_session_start(inp)
    if event == "UserPromptSubmit" and "--watch" in args:
        return watch(inp)
    if event == "UserPromptSubmit":
        return on_prompt(inp)
    if event == "PreToolUse":
        return on_pre_tool(inp)
    if event == "PostToolUse":
        return on_tool(inp)
    if event == "Stop" and "--break-watch" in args:
        return break_watch(inp)
    if event == "Stop":
        return on_stop(inp)
    if event == "SessionEnd":
        return on_session_end(inp)
    return 0
