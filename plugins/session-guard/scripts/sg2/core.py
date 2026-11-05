# SESSION-GUARD: 2.0 | component: sg2-core | version: 2.0.0
"""Paths, safe file writes, the cross-process lock and the id formats shared by every module.

Machine state lives in ~/.claude/session_guard_v2/ (v1.4 used ~/.claude/session_guard/: the two never share a
file, so either version can be identified and removed on its own). Project state lives in the repository at
.claude/session-guard2/ (committed, so a hand-off reaches every place that checks the repository out).
"""
import json
import os
import re
import secrets
import time
from datetime import datetime, timezone

K = 1000


def home():
    return os.path.expanduser("~")


def claude_dir():
    return os.path.join(home(), ".claude")


def state_dir():
    return os.path.join(claude_dir(), "session_guard_v2")


def v1_state_dir():
    return os.path.join(claude_dir(), "session_guard")


PROJECT_DIRNAME = os.path.join(".claude", "session-guard2")


def project_dir(root):
    return os.path.join(root, PROJECT_DIRNAME)


def now():
    return time.time()


def iso(epoch=None):
    return datetime.fromtimestamp(epoch if epoch is not None else now(), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def today():
    return datetime.now().strftime("%Y-%m-%d")


def to_time(stamp):
    try:
        return datetime.fromisoformat(str(stamp).replace("Z", "+00:00")).timestamp()
    except (AttributeError, ValueError):
        return None


# --- ids ----------------------------------------------------------------------------
PROJECT_ID = re.compile(r"^PRJ-[0-9A-F]{8}$")
SESSION_ID = re.compile(r"^S-\d{8}-[0-9a-f]{6}(?:-[BI]\d+)*$")   # -B<n> / -I<n>: a bound / independent child session
TASK_ID = re.compile(r"^TASK-\d{3,}(-\d+)*$")
TASK_ID_ANY = re.compile(r"\bTASK-\d{3,}(?:-\d+)*\b")  # task ids inside a text (a subagent prompt)
IDEA_ID = re.compile(r"^IDEA-\d{3,}$")


def new_project_id():
    return "PRJ-" + secrets.token_hex(4).upper()


def new_session_id():
    return "S-%s-%s" % (datetime.now().strftime("%Y%m%d"), secrets.token_hex(3))


def safe_name(text):
    return re.sub(r"[^A-Za-z0-9-]", "_", text or "none")


# --- files ----------------------------------------------------------------------------
def read_json(path, default=None):
    try:
        with open(path, encoding="utf-8-sig") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return default
    except (OSError, ValueError):
        return default


def write_json(path, data):
    """Atomic: a reader sees the old file or the new one, never half of one."""
    write_text(path, json.dumps(data, ensure_ascii=False, indent=1) + "\n")


def write_text(path, text):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = "%s.%d.%s.tmp" % (path, os.getpid(), secrets.token_hex(2))
    with open(tmp, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    for attempt in range(50):  # Windows: a reader holding the file makes replace fail for a moment
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            time.sleep(0.02 * (attempt + 1))
    os.remove(tmp)
    raise OSError("could not replace " + path)


def append_text(path, text):
    """Append only: a large archive is never rewritten in one go."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="") as fh:
        fh.write(text)


def read_text(path):
    try:
        with open(path, encoding="utf-8-sig", errors="replace") as fh:
            return fh.read()
    except OSError:
        return None


class Busy(Exception):
    pass


class FileLock:
    """Cross-process lock: an O_EXCL lock file holding pid and time. A lock older than STALE seconds (its holder
    crashed) is broken. Every read-modify-write of a store happens inside one, so no write is lost and nobody
    silently overwrites a newer state ("last writer wins" never happens)."""
    STALE = 30.0

    def __init__(self, path, timeout=15.0):
        self.path, self.timeout, self.fd = path, timeout, None

    def __enter__(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        deadline = time.time() + self.timeout
        while True:
            try:
                self.fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(self.fd, ("%d %f" % (os.getpid(), time.time())).encode())
                return self
            except (FileExistsError, PermissionError):  # Windows: a lock file being deleted refuses access
                try:
                    if time.time() - os.path.getmtime(self.path) > self.STALE:
                        os.remove(self.path)
                        continue
                except OSError:
                    continue
                if time.time() > deadline:
                    raise Busy("lock held: " + self.path)
                time.sleep(0.01 + secrets.randbelow(20) / 1000.0)

    def __exit__(self, *exc):
        try:
            os.close(self.fd)
        finally:
            try:
                os.remove(self.path)
            except OSError:
                pass
        return False


# --- projects ---------------------------------------------------------------------------
def key(path):
    return os.path.normcase(os.path.abspath(path))


def inside(path, parent):
    p, q = key(path), key(parent)
    return p == q or p.startswith(q.rstrip("\\/") + os.sep)


def exists_ci(base, rel):
    """The real path of base/rel matched without regard to letter case, or None (from v1.4)."""
    path = base
    for part in rel.replace("\\", "/").split("/"):
        try:
            names = os.listdir(path)
        except OSError:
            return None
        match = [n for n in names if n.lower() == part.lower()]
        if not match:
            return None
        path = os.path.join(path, part if part in match else match[0])
    return path


def usable_root(path):
    """The folder as a project root, or None for home, a drive root, ~/.claude and system folders (from v1.4)."""
    if not path or not os.path.isdir(path):
        return None
    path = os.path.abspath(path)
    if key(path) == key(home()) or os.path.dirname(path) == path or inside(path, claude_dir()):
        return None
    for name in ("SystemRoot", "ProgramFiles", "ProgramFiles(x86)", "ProgramData"):
        if os.environ.get(name) and inside(path, os.environ[name]):
            return None
    return path


def main_checkout(root):
    """A git worktree belongs to its main checkout (same repository, same project id) (from v1.4)."""
    try:
        with open(os.path.join(root, ".git"), encoding="utf-8") as fh:
            found = re.match(r"gitdir:\s*(.+)", fh.read().strip())
    except OSError:
        return root
    if found:
        gitdir = os.path.abspath(os.path.join(root, found.group(1).strip()))
        if os.path.basename(os.path.dirname(gitdir)) == "worktrees":
            return os.path.dirname(os.path.dirname(os.path.dirname(gitdir)))
    return root


def project_root(inp):
    """CLAUDE_PROJECT_DIR; without it the nearest folder up with CLAUDE.md, .git or a 2.0 project record."""
    if os.environ.get("CLAUDE_PROJECT_DIR"):
        return usable_root(os.environ["CLAUDE_PROJECT_DIR"])
    start = os.path.abspath((inp or {}).get("cwd") or os.getcwd())
    path = start
    while usable_root(path):
        if exists_ci(path, "CLAUDE.md") or os.path.exists(os.path.join(path, ".git")) or \
                os.path.isfile(os.path.join(project_dir(path), "store.json")):
            return path
        path = os.path.dirname(path)
    return usable_root(start)


def config_path():
    return os.path.join(state_dir(), "config.json")


def load_config():
    cfg = read_json(config_path(), {})
    return cfg if isinstance(cfg, dict) else {}


def save_config(cfg):
    write_json(config_path(), cfg)


def in_cloud():
    return os.environ.get("CLAUDE_CODE_REMOTE") == "true"


def place():
    return "cloud" if in_cloud() else "computer"
