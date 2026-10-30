"""Copies Session Guard into a project repository so it also runs in cloud sessions.

Cloud sessions do not install plugins, but they do run the repository's own
.claude/settings.json hooks and .claude/skills. This script adds:
    <repo>/.claude/session-guard/session_guard.py
    <repo>/.claude/skills/session-guard/SKILL.md
    hooks in <repo>/.claude/settings.json (merged, existing settings kept)
The project copy runs with --project: on a machine where the plugin is installed
it stays quiet, so the guard never runs twice. Safe to run again (updates files,
adds no duplicate hooks).

Usage: python cloud_setup.py [repo_dir]      (default: current directory)
"""
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.dirname(HERE)
COMMAND = ('if [ "$OS" = "Windows_NT" ]; then python -I "$CLAUDE_PROJECT_DIR/.claude/session-guard/session_guard.py" --project; '
           'else python3 -I "$CLAUDE_PROJECT_DIR/.claude/session-guard/session_guard.py" --project; fi')
EVENTS = {"SessionStart": None, "UserPromptSubmit": None, "PostToolUse": "*"}


def main():
    repo = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else os.getcwd())
    claude = os.path.join(repo, ".claude")
    os.makedirs(os.path.join(claude, "session-guard"), exist_ok=True)
    os.makedirs(os.path.join(claude, "skills", "session-guard"), exist_ok=True)
    shutil.copyfile(os.path.join(HERE, "session_guard.py"), os.path.join(claude, "session-guard", "session_guard.py"))
    shutil.copyfile(os.path.join(PLUGIN, "skills", "session-guard", "SKILL.md"),
                    os.path.join(claude, "skills", "session-guard", "SKILL.md"))

    path = os.path.join(claude, "settings.json")
    settings = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            settings = json.load(fh)  # a broken file stops here instead of being overwritten
    hooks = settings.setdefault("hooks", {})
    added = []
    for event, matcher in EVENTS.items():
        groups = hooks.setdefault(event, [])
        present = any("session-guard/session_guard.py" in (h.get("command") or "")
                      for g in groups for h in g.get("hooks", []))
        if present:
            continue
        group = {"hooks": [{"type": "command", "command": COMMAND, "timeout": 10}]}
        if matcher:
            group = {"matcher": matcher, **group}
        groups.append(group)
        added.append(event)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(settings, fh, indent=2, ensure_ascii=False)
        fh.write("\n")

    print("Session Guard copied into %s" % repo)
    print("  .claude/session-guard/session_guard.py, .claude/skills/session-guard/SKILL.md")
    print("  hooks added: %s" % (", ".join(added) if added else "none (already present)"))
    print("Commit these files; cloud sessions of this repository will then run Session Guard.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
