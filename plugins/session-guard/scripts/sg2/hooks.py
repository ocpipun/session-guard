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
import json
import os
import re
import sys
import time

from . import SG_VERSION, core, legacy, measure, messages, migrate, render, rules, sessions, store
from .messages import msg

ENTRY = None          # set by session_guard.py
PROJECT_MODE = False  # repository copy (cloud)
WATCH_SECONDS = int(os.environ.get("SESSION_GUARD_WATCH_SECONDS") or 24 * 3600)
WATCH_STEP = 60
EXECUTIVE = "session-guard-executive"
STORE_DIR_MARK = core.PROJECT_DIRNAME.replace("\\", "/")
STATUS_LONG = ("STATUS.md IS %d LINES (LIMIT 50). Before any new task: move old notes out of the \"Notes\" section "
               "(a line that will matter later goes to STATUS_ARCHIVE.md as one dated line) and close finished tasks. "
               "If open work keeps it over 50, refuse new tasks until the pending ones are finished.")
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
    mine = store.owned_open(data, sid)
    out = ["Your tasks: " + (", ".join("%s (%s)" % (t["id"], t["status"]) for t in mine) or "none")]
    can = store.claimable(data, sid)
    if len(can) > 1:
        out.append("SEVERAL TASKS CAN BE TAKEN OVER (%s). Do not choose one yourself: ask the user which to continue "
                   "(read each with `sg task show <id>`)." % ", ".join(
                       "%s%s" % (t["id"], " hand-off" if t["handoff"] else " orphaned/free") for t in can))
    elif can:
        t = can[0]
        out.append("One task can be taken over: %s \"%s\"%s. If the user's message is about it, read it (`sg task show "
                   "%s`) and claim it; otherwise ask." % (t["id"], t["title"], " (hand-off waiting)" if t["handoff"]
                                                          else "", t["id"]))
    return out


def start_context(root, rec, data, source):
    parts = [rules.RULES]
    if not PROJECT_MODE:
        res = rules.ensure_global_block()
        if res == "broken":
            parts.append("WARNING: the session-guard2 markers in ~/.claude/CLAUDE.md are damaged; tell the user "
                         "(skill setup repairs it).")
    head = "[Session Guard 2.0] SESSION-ID %s (%s, %s) · command: sg = %s" % (
        rec["sg_id"], core.place(), "resumed" if source == "resume" else "new", sg_cmd())
    if data:
        head += " · PROJECT-ID %s (%s)" % (data["project_id"], root)
        parts.append("\n".join([head] + task_lines(data, rec["sg_id"])))
        status = core.exists_ci(root, "docs/STATUS.md")
        if status:
            parts.append("docs/STATUS.md (already loaded, do not read again):\n\n" + (core.read_text(status) or "")[:8000])
        n = render.status_lines(root)
        if n > render.STATUS_MAX_LINES:
            parts.append("[Session Guard] " + STATUS_LONG % n)
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
    rec, known = sessions.load(cid, reading.ids)
    if not known:
        rec["sg_id"] = core.new_session_id()
        rec["born"] = core.now() if source in ("startup", "clear", "fork") else (reading.first_at or 0)
    rec.update(self=ENTRY, root=root, place=core.place())
    data = store.load(root) if root else None
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
    sessions.save(rec, reading.ids)
    if sessions.closed(rec) or hit:
        text = lock_text(rec) or quota_text(rec["locks"]["quota"])
        return emit(ctx("SessionStart", "[Session Guard 2.0] This session is locked: %s Do no work; every prompt is "
                                        "blocked." % text, text))
    write_env(rec, root)
    if data:
        try:
            store.mutate(root, lambda d: store.register_session(d, rec["sg_id"], cid, core.place()), render=False)
            data = store.load(root)
        except (store.Refused, core.Busy):
            pass
    elif root:
        try:
            migrate.note_seen(root)
        except (OSError, core.Busy):
            pass
    out = start_context(root, rec, data, source)
    mig = migrate.load()
    if mig["phase"] == "paused":
        out += ("\n\n[Session Guard 2.0] INSTALLATION PAUSED (%s). Only \"continue installation\" in %s continues it; "
                "until then every prompt is blocked." % ((mig.get("paused") or {}).get("reason"), EXECUTIVE))
    user = msg("migration_pending") if root and not data and source == "startup" else None
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
        found = re.search(r" to (S-\d{8}-[0-9a-f]{6})", srec.get("lock_reason") or "")
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
    rec, known = sessions.load(cid, reading.ids)
    if not known or (rec.get("self") and rec["self"] != ENTRY and not PROJECT_MODE):
        if not PROJECT_MODE and act == "active":
            return block(msg("restart_plugin"))
        rec["sg_id"] = rec.get("sg_id") or core.new_session_id()
        rec["born"] = rec.get("born") or core.now()
        rec["self"] = ENTRY
    if act == "setup":
        sessions.save(rec, reading.ids)
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
            sessions.save(rec, reading.ids)
            return block(msg("migration_paused"))
    data = store.load(root) if root else None
    if closed_now(rec, data):
        sessions.save(rec, reading.ids)
        return block(lock_text(rec))
    quota = rec["locks"].get("quota")
    if quota and measure.quota_cleared(quota):
        del rec["locks"]["quota"]
        quota = None
    if quota:
        sessions.save(rec, reading.ids)
        return block(quota_text(quota))

    notes, user = [], []
    if measure.has_command(measure.LONG, prompt) and not rec["long"]:
        rec["long"] = True
        notes.append("LONG MODE is on for this session (cannot be switched off). Confirm it to the user in one line.")
    long_mode = rec["long"]
    finish = measure.has_command(measure.FINISH, prompt)
    gap = time.time() - reading.last_reply if reading.last_reply else None
    line = "[Session Guard] Context: %s (last reply). Mode: %s. Since last reply: %s.%s" % (
        measure.kfmt(reading.context), "LONG" if long_mode else "normal",
        "unknown" if gap is None else "%d min" % (gap // 60),
        " Background jobs still running: %d (break rule off)." % reading.pending if reading.pending else "")
    limit = measure.LONG_LIMIT if long_mode else measure.NORMAL_LIMIT
    if reading.context is not None and reading.context >= limit and not finish:
        sessions.lock(rec, "context", "context %s" % measure.kfmt(reading.context))
        sessions.save(rec, reading.ids)
        return block(msg("hard_limit", k=measure.kfmt(reading.context)))

    sid = rec["sg_id"]
    if finish:
        sessions.lock(rec, "finish", "finish session")
        notes.append('FINISH SESSION. Do nothing else the message asks. Only skill session-guard "Finish session": %s'
                     ' (measurement: %s --report). The session is locked after this reply.' % (
                         handoff_todo(data, sid), sg_cmd()))
        user.append(msg("finishing"))
    else:
        br = measure.break_decision(reading, long_mode, finish)
        if br and br[0] == "lock":
            sessions.lock(rec, "break", "break over 1 hour, context %s" % measure.kfmt(reading.context))
            if data:
                try:
                    store.mutate(root, lambda d: store.record_user_command(d, sid, raw_prompt))
                except (store.Refused, core.Busy):
                    pass
            notes.append("BREAK RULE (gap %d min, context %s > 100k). Do NOT run the user's command and do no reading or "
                         "research for it. Only skill session-guard \"Break hand-off\": %s. Session Guard stored the "
                         "command word for word. Tell the user to continue in a new session. The session is locked "
                         "after this reply." % (br[1] // 60, measure.kfmt(reading.context), handoff_todo(data, sid)))
            user.append(msg("break_lock", k=measure.kfmt(reading.context)))
        elif br:
            notes.append("BREAK REMINDER: gap over 1 hour (%d min), context %s <= 100k. Remind the user in one line, "
                         "then handle the command normally." % (br[1] // 60, measure.kfmt(reading.context)))
            user.append(msg("break_remind", k=measure.kfmt(reading.context)))
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
                                        else "") + (" " + handoff_todo(data, sid) if "hand-off" in step[2].lower()
                                                    and data else ""))
            if step[1] not in rec["notified"]:
                rec["notified"].append(step[1])
                user.append(msg(step[1]))
        hit = None if PROJECT_MODE else measure.quota_hit()
        if hit:
            quota_lock(rec, hit)
            notes.append(measure.QUOTA_STOP % (dict(measure.WINDOWS)[hit[0]], hit[1]["used_percent"],
                                               measure.clock(hit[1]["resets_at"])))
            user.append(quota_text(rec["locks"]["quota"], "quota_stop"))
        elif measure.needs_reading(rec, False):
            notes.append(measure.READ_USAGE)
    if data:
        restored = restore_status(root)
        if restored:
            notes.append(restored)
            user.append(msg("status_restored"))
        n = render.status_lines(root)
        if n > render.STATUS_MAX_LINES and not finish:
            notes.append(STATUS_LONG % n)
    notes += budget_notes(cid, reading, rec)
    sessions.save(rec, reading.ids)
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
    return handed_lock(rec, data) or legacy_check(rec, data) or home_check(rec, data)


def handoff_todo(data, sid):
    if not data:
        return "hand-off as the skill says (this project is not on 2.0 yet: no task ids)"
    missing = store.missing_handoffs(data, sid)
    mine = [t["id"] for t in store.owned_open(data, sid)]
    if not mine:
        return "you own no open task: no hand-off needed"
    return "tasks you own: %s; hand-off still missing for: %s; then `%s handoff publish`" % (
        ", ".join(mine), ", ".join(missing) or "none", sg_cmd())


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


def on_pre_tool(inp):
    """The task store is changed only through `sg` (lock, ownership, revision): direct edits are refused."""
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
    rec, known = sessions.load(cid, reading.ids)
    if not known or sessions.closed(rec):
        return 0
    quota = rec["locks"].get("quota")
    if quota:
        if measure.quota_cleared(quota):
            del rec["locks"]["quota"]
            sessions.save(rec, reading.ids)
        elif time.time() - quota.get("at", 0) > measure.QUOTA_GRACE:
            text = quota_text(quota, "quota_stopped")
            return emit({"continue": False, "stopReason": text, "systemMessage": text})
        else:
            return 0
    if subagent(inp):
        return 0
    extra = []
    if inp.get("tool_name") in EDIT_TOOLS and os.path.basename(tool_path(inp)).lower() == "status.md" and root \
            and store.is_project(root):
        r = restore_status(root)
        if r:
            extra.append(r)
    turn = "turn24:%s" % reading.prompt_at
    if reading.prompt_at and time.time() - reading.prompt_at > measure.PENDING_MAX and turn not in rec["notified"]:
        rec["notified"].append(turn)
        sessions.save(rec, reading.ids)
        return emit(ctx("PostToolUse", LONG_TURN, msg("stale")))
    step = measure.step_for(reading.context, rec["long"])
    if step and step[1] not in rec["notified"]:
        rec["notified"].append(step[1])
        data = ctx("PostToolUse", "[Session Guard] Context: %s (during work). %s%s" % (
            measure.kfmt(reading.context), step[2], measure.NOTICE_FORM % (step[0] // core.K)), msg(step[1]))
        if step[3]:
            sessions.lock(rec, "context", "context %s" % measure.kfmt(reading.context))
            data.update({"continue": False, "stopReason": msg(step[1])})
        sessions.save(rec, reading.ids)
        return emit(data)
    hit = None if PROJECT_MODE else measure.quota_hit()
    if hit:
        quota_lock(rec, hit)
        sessions.save(rec, reading.ids)
        return emit(ctx("PostToolUse", "[Session Guard] " + measure.QUOTA_STOP % (
            dict(measure.WINDOWS)[hit[0]], hit[1]["used_percent"], measure.clock(hit[1]["resets_at"])),
            quota_text(rec["locks"]["quota"], "quota_stop")))
    extra += budget_notes(cid, reading, rec)
    if measure.needs_reading(rec, True):
        extra.append(measure.READ_USAGE)
    sessions.save(rec, reading.ids)
    if extra:
        return emit(ctx("PostToolUse", "\n".join(extra)))
    return 0


# --- stop / end ----------------------------------------------------------------------------------
def on_stop(inp):
    """After "finish session" or a break hand-off: Claude is kept working (twice at most) while a task it owns has
    no hand-off yet or one not yet published."""
    root = core.project_root(inp)
    if activation(root) != "active" or subagent(inp):
        return 0
    rec, known = sessions.load(inp.get("session_id"))
    if not known or not ({"finish", "break"} & set(rec["locks"])) or rec.get("stop_blocks", 0) >= 2:
        return 0
    data = store.load(root) if root else None
    if not data:
        return 0
    sid = rec["sg_id"]
    missing = store.missing_handoffs(data, sid)
    unpublished = [t["id"] for t in store.owned_open(data, sid) if t["handoff"] and t["handoff"]["state"] == "prepared"]
    if not missing and not unpublished:
        return 0
    rec["stop_blocks"] = rec.get("stop_blocks", 0) + 1
    sessions.save(rec)
    return emit({"decision": "block", "reason": "Session Guard 2.0: before the session closes, %s%s." % (
        "write the hand-off for %s (`%s handoff write <id> --technical ...`)" % (", ".join(missing), sg_cmd())
        if missing else "", "%spublish the hand-offs (`%s handoff publish`)" % ("; then " if missing else "", sg_cmd())
        if missing or unpublished else "")})


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
    rec, known = sessions.load(cid, reading.ids)
    if not known or rec["locks"] or not (reading.pending or reading.stale or reading.turn_open):
        return 0
    sys.stderr.write(WATCH_WAKE)
    return 2


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
    if event == "Stop":
        return on_stop(inp)
    if event == "SessionEnd":
        return on_session_end(inp)
    return 0
