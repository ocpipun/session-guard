# SESSION-GUARD: 2.0 | component: sg2-place | version: 2.0.0
"""Terminal status line (quota readings at zero token cost) and the computer / cloud split of projects.

Status line: the documented status line input carries rate_limits.five_hour / seven_day (used_percentage,
resets_at) for Pro/Max subscribers after the first reply. Session Guard's line is marked "session-guard2-statusline";
a 1.x line ("session-guard-statusline") is re-pointed to 2.0; another tool's line is never touched.

One home per project (v1.4 rule, kept): a computer project that is a git repository gets a one-line hook in its
.claude/settings.json that blocks every prompt of a cloud session of it (CLAUDE_CODE_REMOTE=true, documented);
a cloud project carries the repository copy in .claude/session-guard2/bin/ and its store says home "cloud", so the
plugin on a computer locks its sessions ("home" lock).
"""
import json
import os
import shutil
import sys

from . import core, legacy, measure, rules

STATUSLINE_SIGN = "session-guard2-statusline"
HOME_SIGN = "session-guard2-home"
PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
COPY_DIR = os.path.join(".claude", "session-guard2", "bin")


def settings_path():
    return os.path.join(core.claude_dir(), "settings.json")


def python_cmd(path, args):
    path = path.replace("\\", "/")
    return 'if [ "$OS" = "Windows_NT" ]; then python -I "%s" %s; else python3 -I "%s" %s; fi' % (path, args, path, args)


def statusline_command(entry):
    return python_cmd(entry, "statusline") + " # " + STATUSLINE_SIGN + " SESSION-GUARD: 2.0"


def load_settings(path):
    try:
        with open(path, encoding="utf-8-sig") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        return None  # a broken file is never overwritten
    return data if isinstance(data, dict) else None


def save_settings(path, data):
    core.write_text(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def install_statusline(entry):
    s = load_settings(settings_path())
    if s is None:
        return "settings.json cannot be read: not changed"
    cur = s.get("statusLine")
    cmd = str((cur or {}).get("command") or "")
    if cur and STATUSLINE_SIGN not in cmd and legacy.V1_STATUSLINE not in cmd:
        return "another tool's status line is in place: not changed (no quota readings in the terminal)"
    new = dict(cur or {"type": "command"}, command=statusline_command(entry))
    if cur == new:
        return "already in place"
    s["statusLine"] = new
    save_settings(settings_path(), s)
    return "re-pointed from 1.x" if legacy.V1_STATUSLINE in cmd else "installed"


def uninstall_statusline():
    s = load_settings(settings_path())
    if s is None:
        return "settings.json cannot be read: not changed"
    if STATUSLINE_SIGN not in str((s.get("statusLine") or {}).get("command") or ""):
        return "no Session Guard 2.0 status line"
    s.pop("statusLine")
    save_settings(settings_path(), s)
    return "removed"


def statusline():
    """The status line command: saves the quota numbers Claude Code passes in and prints them."""
    try:
        limits = json.loads(sys.stdin.buffer.read().decode("utf-8", "replace") or "{}").get("rate_limits") or {}
        for source, name in (("five_hour", "five_hour"), ("seven_day", "weekly")):
            win = limits.get(source)
            if isinstance(win, dict):
                measure.save_reading(name, win.get("used_percentage"), win.get("resets_at"), "statusline")
    except Exception:
        pass
    found = measure.readings()
    text = " · ".join("%s %d%%" % (label, found[n]["used_percent"]) for n, label in measure.WINDOWS if n in found)
    sys.stdout.buffer.write(((text or "Session Guard 2.0") + "\n").encode("utf-8"))
    return 0


# --- computer guard (blocks cloud sessions of a computer project) ---------------------------------
def home_guard_command():
    block = json.dumps({"decision": "block", "reason": "Session Guard: this repository is a computer project. Your "
                        "message was not sent. Continue it on the computer."}).replace("'", "\\u0027")
    return "[ \"$CLAUDE_CODE_REMOTE\" = \"true\" ] && printf '%%s' '%s'; exit 0 # %s SESSION-GUARD: 2.0" % (
        block, HOME_SIGN)


def ensure_home_guard(root):
    """Adds (or updates in place, replacing a 1.x guard) the cloud-blocking hook; True when the file changed."""
    if not os.path.exists(os.path.join(root, ".git")):
        return False
    p = os.path.join(root, ".claude", "settings.json")
    s = load_settings(p)
    if s is None or not isinstance(s.setdefault("hooks", {}), dict):
        return False
    groups = s["hooks"].setdefault("UserPromptSubmit", [])
    if not isinstance(groups, list):
        return False
    cmd = home_guard_command()
    old = [h for g in groups if isinstance(g, dict) for h in g.get("hooks", []) if isinstance(h, dict)
           and (HOME_SIGN in str(h.get("command")) or legacy.V1_HOME in str(h.get("command")))]
    if old:
        if all(h.get("command") == cmd for h in old) and len(old) == 1:
            return False
        for h in old[1:]:
            for g in groups:
                if h in g.get("hooks", []):
                    g["hooks"].remove(h)
        old[0]["command"] = cmd
        s["hooks"]["UserPromptSubmit"] = [g for g in groups if not (isinstance(g, dict) and g.get("hooks") == [])]
    else:
        groups.append({"hooks": [{"type": "command", "timeout": 5, "command": cmd}]})
    save_settings(p, s)
    return True


# --- repository copy (cloud projects) --------------------------------------------------------------
def copy_command(args=""):
    return ('S="$CLAUDE_PROJECT_DIR/.claude/session-guard2/bin/scripts/session_guard.py"; [ -f "$S" ] || exit 0; '
            'if [ "$OS" = "Windows_NT" ]; then python -I "$S" --project%s; else python3 -I "$S" --project%s; fi'
            ' # SESSION-GUARD: 2.0' % (args, args))


def copy_hooks():
    c = lambda a="", **kw: dict({"type": "command", "command": copy_command(a), "timeout": 10}, **kw)
    return [("SessionStart", None, c()), ("UserPromptSubmit", None, c()),
            ("UserPromptSubmit", None, c(" --watch", timeout=90000, **{"async": True, "asyncRewake": True})),
            ("PreToolUse", "Edit|Write|MultiEdit|NotebookEdit|Agent|Task|Bash", c()), ("PostToolUse", "*", c()),
            ("Stop", None, c()), ("SessionEnd", None, c(timeout=2))]


def install_copy(root):
    """Copies the plugin into <repo>/.claude/session-guard2/bin/ and merges its hooks into .claude/settings.json.
    1.x copy hooks are replaced (their files are removed only with the user's approval). Idempotent."""
    p = os.path.join(root, ".claude", "settings.json")
    s = load_settings(p)
    if s is None:
        return "NOT INSTALLED: .claude/settings.json cannot be read"
    if any(HOME_SIGN in c or legacy.V1_HOME in c for _, c in legacy.hook_commands(s)):
        return "NOT INSTALLED: this repository is a computer project (it has the computer guard)"
    dst = os.path.join(root, COPY_DIR)
    if os.path.isdir(dst):
        shutil.rmtree(dst)
    for sub in ("scripts", "skills", "messages", ".claude-plugin"):
        shutil.copytree(os.path.join(PLUGIN_ROOT, sub), os.path.join(dst, sub),
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copyfile(os.path.join(PLUGIN_ROOT, "manifest.json"), os.path.join(dst, "manifest.json"))
    skills = os.path.join(root, ".claude", "skills")
    for name, folder in (("session-guard", "session-guard"), ("setup", "session-guard-setup")):
        text = core.read_text(os.path.join(PLUGIN_ROOT, "skills", name, "SKILL.md"))
        core.write_text(os.path.join(skills, folder, "SKILL.md"), text.replace("name: %s\n" % name, "name: %s\n" % folder, 1))
    hooks = s.setdefault("hooks", {})
    wanted = {h["command"] for _, _, h in copy_hooks()}
    for event in list(hooks):
        groups = hooks[event] if isinstance(hooks[event], list) else []
        for g in groups:
            if isinstance(g, dict) and isinstance(g.get("hooks"), list):
                g["hooks"] = [h for h in g["hooks"] if not (isinstance(h, dict) and (
                    legacy.V1_COPY.replace("\\", "/") in str(h.get("command")) or
                    ("session-guard2/bin" in str(h.get("command")) and h.get("command") not in wanted)))]
        hooks[event] = [g for g in groups if not (isinstance(g, dict) and g.get("hooks") == [])]
        if not hooks[event]:
            del hooks[event]
    added = 0
    for event, matcher, hook in copy_hooks():
        groups = hooks.setdefault(event, [])
        if any(h.get("command") == hook["command"] for g in groups for h in g.get("hooks", [])):
            continue
        groups.append(dict({"matcher": matcher} if matcher else {}, hooks=[hook]))
        added += 1
    save_settings(p, s)
    core.write_json(os.path.join(core.project_dir(root), "setup.json"),
                    {"signature": "SESSION-GUARD: 2.0", "rules": rules.RULES_VERSION, "session_guard": "2.0.0",
                     "date": core.today()})
    return "copied to %s; hooks added: %d" % (COPY_DIR.replace("\\", "/"), added)


def remove_project(root, remove_store=False):
    """Takes Session Guard out of one project (computer or cloud repository), keeping its history readable:
    STATUS.md keeps its last task list as plain text, the archive, backlog and completed list stay; Session Guard's
    hooks (computer guard, repository copy hooks), its copy, its skills copies and setup marker go; the task store
    only with remove_store. Nothing is committed or pushed here."""
    from . import render
    done = []
    path = render.doc(root, "STATUS.md")
    text = core.read_text(path)
    if text:
        before, body, after = render.split_status(text)
        if body is not None:
            note = "<!-- Session Guard 2.0 removed %s: the last generated task list, now plain text -->" % core.today()
            core.write_text(path, render.tidy(before + note + "\n" + body + "\n" + after))
            done.append("STATUS.md: task block kept as plain text")
    p = os.path.join(root, ".claude", "settings.json")
    s = load_settings(p)
    if s and isinstance(s.get("hooks"), dict):
        n = 0
        for event in list(s["hooks"]):
            groups = s["hooks"][event] if isinstance(s["hooks"][event], list) else []
            for g in groups:
                if isinstance(g, dict) and isinstance(g.get("hooks"), list):
                    keep = [h for h in g["hooks"] if not (isinstance(h, dict) and (
                        HOME_SIGN in str(h.get("command")) or "session-guard2/bin" in str(h.get("command"))))]
                    n += len(g["hooks"]) - len(keep)
                    g["hooks"] = keep
            s["hooks"][event] = [g for g in groups if not (isinstance(g, dict) and g.get("hooks") == [])]
            if not s["hooks"][event]:
                del s["hooks"][event]
        if not s["hooks"]:
            del s["hooks"]
        if n:
            save_settings(p, s)
            done.append(".claude/settings.json: %d Session Guard hook(s) removed" % n)
    for rel in (COPY_DIR, os.path.join(".claude", "session-guard2", "setup.json")):
        q = os.path.join(root, rel)
        if os.path.isdir(q):
            shutil.rmtree(q)
            done.append(rel.replace("\\", "/") + " removed")
        elif os.path.isfile(q):
            os.remove(q)
            done.append(rel.replace("\\", "/") + " removed")
    for folder in ("session-guard", "session-guard-setup"):
        q = os.path.join(root, ".claude", "skills", folder)
        if "SESSION-GUARD: 2.0" in (core.read_text(os.path.join(q, "SKILL.md")) or ""):
            shutil.rmtree(q)
            done.append(".claude/skills/%s removed" % folder)
    if remove_store and os.path.isdir(core.project_dir(root)):
        shutil.rmtree(core.project_dir(root))
        done.append(".claude/session-guard2 (task store) removed")
    return "; ".join(done) or "nothing of Session Guard 2.0 found"


def purge_state():
    """~/.claude/session_guard_v2 (session records, quota readings, migration checkpoint, backups) is deleted."""
    p = core.state_dir()
    if not os.path.isdir(p):
        return "none"
    shutil.rmtree(p, ignore_errors=True)
    return "removed " + p


def plugin_installed(self_path):
    """A Session Guard script under ~/.claude/plugins other than this copy: the repository copy stays quiet."""
    import glob
    pattern = os.path.join(core.claude_dir(), "plugins", "**", "session_guard.py")
    return any(os.path.abspath(p) != os.path.abspath(self_path) for p in glob.glob(pattern, recursive=True))
