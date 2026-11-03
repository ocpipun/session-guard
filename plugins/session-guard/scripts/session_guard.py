# SESSION-GUARD: 2.0 | component: sg-entry | version: 2.0.0
"""Session Guard 2.0 entry point: hook events (JSON on stdin) and `sg` commands.

    python session_guard.py                      a hook event (SessionStart, UserPromptSubmit, PreToolUse,
                                                 PostToolUse, Stop, SessionEnd); --watch = the 24-hour watch
    python session_guard.py task|idea|handoff|migrate|install|manifest|report ...   commands (see --help)
    --project                                    repository copy mode (cloud)
The file keeps the 1.x name on purpose (see sg2/legacy.py). Hooks run with python -I, which leaves the script's
folder off sys.path, so it is added here.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from sg2 import cli, hooks  # noqa: E402

COMMANDS = ("task", "idea", "handoff", "migrate", "install", "manifest", "report", "-h", "--help")


def main():
    args = sys.argv[1:]
    hooks.PROJECT_MODE = "--project" in args
    hooks.ENTRY = os.path.abspath(__file__)
    args = [a for a in args if a != "--project"]
    if args and args[0] in COMMANDS:
        return cli.run(args)
    try:
        inp = json.loads(sys.stdin.buffer.read().decode("utf-8", errors="replace") or "{}")
        return hooks.dispatch(inp, args)
    except Exception:  # the guard must never break the user's work
        return 0


if __name__ == "__main__":
    sys.exit(main())
