"""Copies Session Guard into a project repository so it also runs in cloud sessions.

Cloud sessions do not install plugins, but they do run the repository's own
.claude/settings.json hooks and .claude/skills. This script adds:
    <repo>/.claude/session-guard/session_guard.py
    <repo>/.claude/session-guard/setup.json          (review approved, rules version)
    <repo>/.claude/skills/session-guard/SKILL.md
    <repo>/.claude/skills/session-guard-setup/SKILL.md
    hooks in <repo>/.claude/settings.json (merged, existing settings kept)
    the missing CLAUDE.md, docs/STATUS.md, docs/STATUS_ARCHIVE.md (existing files untouched)
It refuses to run until the repository passed the Session Guard project review
(skill setup), because the cloud copy is only allowed where no conflicting rule
is left. The project copy runs with --project: on a machine where the plugin is
installed it stays quiet, so the guard never runs twice. Safe to run again
(updates files, adds no duplicate hooks).

Usage: python cloud_setup.py [--reviewed] [repo_dir]      (default: current directory)
    --reviewed   the user approved the project review in this session (CLOUD.md: install from a cloud
                 session, where the plugin and its setup record do not exist)
"""
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import session_guard as sg  # noqa: E402  (same folder; -I leaves it off sys.path)

COMMAND = ('if [ "$OS" = "Windows_NT" ]; then python -I "$CLAUDE_PROJECT_DIR/.claude/session-guard/session_guard.py" --project; '
           'else python3 -I "$CLAUDE_PROJECT_DIR/.claude/session-guard/session_guard.py" --project; fi')
WATCH = COMMAND.replace(" --project;", " --project --watch;")
# (event, matcher, hook): the 24-hour watch runs in the background and may wake Claude (asyncRewake, exit code 2).
HOOKS = [("SessionStart", None, {"type": "command", "command": COMMAND, "timeout": 10}),
         ("UserPromptSubmit", None, {"type": "command", "command": COMMAND, "timeout": 10}),
         ("UserPromptSubmit", None, {"type": "command", "command": WATCH, "async": True, "asyncRewake": True, "timeout": 90000}),
         ("PostToolUse", "*", {"type": "command", "command": COMMAND, "timeout": 10})]


def copy_skill(name, folder):
    """Copies a plugin skill; in a repository it is named after its folder (no plugin prefix there)."""
    with open(os.path.join(PLUGIN, "skills", name, "SKILL.md"), encoding="utf-8") as fh:
        text = fh.read().replace("name: %s\n" % name, "name: %s\n" % folder, 1)
    os.makedirs(folder_path(folder), exist_ok=True)
    with open(os.path.join(folder_path(folder), "SKILL.md"), "w", encoding="utf-8") as fh:
        fh.write(text)


def folder_path(folder):
    return os.path.join(REPO, ".claude", "skills", folder)


def main():
    global REPO
    args = [a for a in sys.argv[1:] if a != "--reviewed"]
    REPO = os.path.abspath(args[0] if args else os.getcwd())
    cfg = sg.load_config()
    status = cfg["projects"].get(sg.key(REPO))
    if status != "approved" and "--reviewed" not in sys.argv:
        if sg.review_items(REPO):
            print("Not copied: %s has not passed the Session Guard project review (status: %s)." % (REPO, status or "not done"))
            print("Run the project review first (skill session-guard:setup, \"Project review\").")
            return 1
        cfg["projects"][sg.key(REPO)] = "approved"  # nothing to review; the CLAUDE.md made below is Session Guard's
        sg.save_config(cfg)
    claude = os.path.join(REPO, ".claude")
    os.makedirs(os.path.join(claude, "session-guard"), exist_ok=True)
    shutil.copyfile(os.path.join(HERE, "session_guard.py"), os.path.join(claude, "session-guard", "session_guard.py"))
    copy_skill("session-guard", "session-guard")
    copy_skill("setup", "session-guard-setup")
    sg.write_marker(REPO, "approved")
    made = sg.create_project_files(REPO)

    path = os.path.join(claude, "settings.json")
    settings = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            settings = json.load(fh)  # a broken file stops here instead of being overwritten
    hooks = settings.setdefault("hooks", {})
    added = []
    for event, matcher, hook in HOOKS:
        groups = hooks.setdefault(event, [])
        present = any(h.get("command") == hook["command"] for g in groups for h in g.get("hooks", []))
        if present:
            continue
        group = {"hooks": [hook]}
        if matcher:
            group = {"matcher": matcher, **group}
        groups.append(group)
        added.append(event)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(settings, fh, indent=2, ensure_ascii=False)
        fh.write("\n")

    print("Session Guard copied into %s" % REPO)
    print("  .claude/session-guard/ (script, setup.json), .claude/skills/session-guard/, .claude/skills/session-guard-setup/")
    print("  hooks added: %s" % (", ".join(added) if added else "none (already present)"))
    print("  project files created: %s" % (", ".join(made) if made else "none (all present)"))
    print("Commit these files; cloud sessions of this repository will then run Session Guard.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
