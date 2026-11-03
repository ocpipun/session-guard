# SESSION-GUARD: 2.0 | component: sg2-legacy | version: 2.0.0
"""Finds what Session Guard 1.x left on this computer or in a project, so old and new never guard the same
session at once and the install can tell exactly which version manages which file.

1.x marks (never used by 2.0):
    ~/.claude/session_guard/                          state, setup.json, quota readings
    <!-- session-guard:begin ... -->                  global rules block in ~/.claude/CLAUDE.md
    "session-guard-statusline"                        status line command
    "session-guard-home"                              computer guard hook in <repo>/.claude/settings.json
    <repo>/.claude/session-guard/session_guard.py     cloud copy
2.0 marks: ~/.claude/session_guard_v2/, <!-- session-guard2:begin, "session-guard2-statusline",
"session-guard2-home", <repo>/.claude/session-guard2/.
The 2.0 entry script keeps the name session_guard.py on purpose: a 1.x cloud copy checked out on a computer
stays quiet when it finds a session_guard.py under ~/.claude/plugins (v1.4 plugin_installed()).
"""
import os

from . import core

V1_BLOCK = "<!-- session-guard:begin"
V1_BLOCK_END = "<!-- session-guard:end -->"
V1_STATUSLINE = "session-guard-statusline"
V1_HOME = "session-guard-home"
V1_COPY = os.path.join(".claude", "session-guard", "session_guard.py")


def installed_plugins():
    data = core.read_json(os.path.join(core.claude_dir(), "plugins", "installed_plugins.json"), {})
    plugins = data.get("plugins") if isinstance(data, dict) else None
    return plugins if isinstance(plugins, dict) else {}


def v1_plugins():
    """Installed Session Guard plugin entries whose version is 1.x and whose files still exist."""
    found = []
    for name, entries in installed_plugins().items():
        if "session-guard" not in name or not isinstance(entries, list):
            continue
        for e in entries:
            if isinstance(e, dict) and str(e.get("version", "")).startswith("1.") and \
                    os.path.isdir(str(e.get("installPath") or "")):
                found.append({"plugin": name, "version": e["version"], "path": e["installPath"],
                              "scope": e.get("scope")})
    return found


def settings(path):
    data = core.read_json(path, {})
    return data if isinstance(data, dict) else {}


def hook_commands(data):
    hooks = data.get("hooks") if isinstance(data.get("hooks"), dict) else {}
    return [(event, str(h.get("command") or "")) for event, groups in hooks.items() if isinstance(groups, list)
            for g in groups if isinstance(g, dict) for h in g.get("hooks", []) if isinstance(h, dict)]


def global_report():
    gmd = core.read_text(os.path.join(core.claude_dir(), "CLAUDE.md")) or ""
    st = settings(os.path.join(core.claude_dir(), "settings.json"))
    line = str((st.get("statusLine") or {}).get("command") or "")
    v1_hooks = [c for c in hook_commands(st) if "session_guard.py" in c[1] and "session_guard_v2" not in c[1]
                and "session-guard2" not in c[1]]
    return {"v1_plugins": v1_plugins(),
            "v1_state_dir": os.path.isdir(core.v1_state_dir()),
            "v1_global_block": V1_BLOCK in gmd,
            "v1_statusline": V1_STATUSLINE in line and "session-guard2-statusline" not in line,
            "v1_global_hooks": v1_hooks}


def project_report(root):
    st = settings(os.path.join(root, ".claude", "settings.json"))
    cmds = hook_commands(st)
    return {"v1_cloud_copy": os.path.isfile(os.path.join(root, V1_COPY)),
            "v1_home_guard": any(V1_HOME in c and "session-guard2-home" not in c for _, c in cmds),
            "v1_marker": os.path.isfile(os.path.join(root, ".claude", "session-guard", "setup.json")),
            "v2_store": os.path.isfile(os.path.join(core.project_dir(root), "store.json"))}


def v1_active():
    """True while a 1.x plugin is installed: then 2.0 stays passive (no double guard)."""
    return bool(v1_plugins())


def conflicts():
    """The managed-by table the install shows: what belongs to 1.x, what to 2.0, what is the user's."""
    g = global_report()
    rows = []
    for p in g["v1_plugins"]:
        rows.append(("plugin %s %s" % (p["plugin"], p["version"]), "1.x", "replaced by `claude plugin update` (same id)"))
    if g["v1_global_block"]:
        rows.append(("~/.claude/CLAUDE.md block <!-- session-guard:begin", "1.x",
                     "removed after the 2.0 block is written and checked"))
    if g["v1_statusline"]:
        rows.append(("~/.claude/settings.json statusLine", "1.x", "re-pointed to 2.0 (other tools' lines untouched)"))
    for event, cmd in g["v1_global_hooks"]:
        rows.append(("~/.claude/settings.json hook %s" % event, "1.x (hand-made copy)", "listed for the user's approval"))
    if g["v1_state_dir"]:
        rows.append(("~/.claude/session_guard/", "1.x", "kept read-only (its locks are honoured) until cleanup"))
    return rows
