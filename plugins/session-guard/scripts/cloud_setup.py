"""Copies Session Guard into a project repository so it also runs in cloud sessions.

Cloud sessions do not install plugins, but they do run the repository's own
.claude/settings.json hooks and .claude/skills. This script adds:
    <repo>/.claude/session-guard/session_guard.py
    <repo>/.claude/skills/session-guard/SKILL.md
    <repo>/.claude/skills/session-guard-setup/SKILL.md
    hooks in <repo>/.claude/settings.json (merged, existing settings kept)
The project review (conflicting rules, similar files) and the missing CLAUDE.md,
docs/STATUS.md, docs/STATUS_ARCHIVE.md come in the next cloud session: the copy
starts the review by itself, and stays inactive until the user approves it.
The project copy runs with --project: on a machine where the plugin is
installed it stays quiet, so the guard never runs twice. Safe to run again
(updates files, adds no duplicate hooks).

Usage: python cloud_setup.py [repo_dir]      (default: current directory; v1.1's --reviewed is ignored)
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
    claude = os.path.join(REPO, ".claude")
    os.makedirs(os.path.join(claude, "session-guard"), exist_ok=True)
    shutil.copyfile(os.path.join(HERE, "session_guard.py"), os.path.join(claude, "session-guard", "session_guard.py"))
    folder = os.path.join(claude, "session-guard", "messages")  # the plugin's translations; the user's go here too
    os.makedirs(folder, exist_ok=True)
    for name in os.listdir(os.path.join(PLUGIN, "messages")):
        shutil.copyfile(os.path.join(PLUGIN, "messages", name), os.path.join(folder, name))
    copy_skill("session-guard", "session-guard")
    copy_skill("setup", "session-guard-setup")

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
    print("Commit these files. In a new cloud session of this repository Session Guard starts with its project review.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
