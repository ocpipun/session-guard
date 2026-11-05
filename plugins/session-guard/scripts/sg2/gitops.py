# SESSION-GUARD: 2.0 | component: sg2-gitops | version: 2.0.0
"""Git steps: rollback point, committing only Session Guard's own paths, push and push verification.

A push counts only when `git ls-remote` shows the remote branch at the local commit. Commits name their paths
(`git commit -- <paths>`): other files of the user are never staged. CLAUDE.md and .claude/settings.json are among
Session Guard's paths (it writes rule ids and hooks there), so the user's own edits in those two files go into
Session Guard's commits too.
"""
import os
import re
import subprocess
import tempfile

SG_PATHS = (".claude/session-guard2", "docs/STATUS.md", "docs/STATUS_ARCHIVE.md", "docs/TASK_BACKLOG.md",
            "docs/IDEA_TASKS.md", "docs/COMPLETED_TASKS.md", "CLAUDE.md", ".claude/settings.json")


QUIET = {"GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never"}  # no credential prompt or window in a hook


def git(root, *args, timeout=120, strip=True, quiet=False):
    try:
        r = subprocess.run(["git", "-C", root] + list(args), capture_output=True, timeout=timeout,
                           stdin=subprocess.DEVNULL, env=dict(os.environ, **QUIET) if quiet else None)
    except (OSError, subprocess.SubprocessError) as e:
        return 1, "", str(e)
    out = r.stdout.decode("utf-8", "replace")
    return r.returncode, out.strip() if strip else out, r.stderr.decode("utf-8", "replace").strip()


def is_repo(root):
    return git(root, "rev-parse", "--is-inside-work-tree")[1] == "true"


def head(root):
    rc, out, _ = git(root, "rev-parse", "HEAD")
    return out if rc == 0 else None


def branch(root):
    return git(root, "branch", "--show-current")[1] or None


def remote(root):
    rc, out, _ = git(root, "remote")
    names = out.split()
    return "origin" if "origin" in names else (names[0] if names else None)


def facts(root):
    """What a migration survey reports about a repository."""
    if not is_repo(root):
        return {"git": False}
    rem = remote(root)
    _, status, _ = git(root, "status", "--porcelain")
    _, log, _ = git(root, "log", "-8", "--format=%h %ad %s", "--date=short")
    _, branches, _ = git(root, "branch", "-a", "--format=%(refname:short)")
    ahead = None
    if rem and branch(root):
        rc, out, _ = git(root, "rev-list", "--count", "%s/%s..HEAD" % (rem, branch(root)))
        ahead = int(out) if rc == 0 and out.isdigit() else None
    return {"git": True, "branch": branch(root), "head": head(root), "remote": rem,
            "remote_url": git(root, "remote", "get-url", rem)[1] if rem else None,
            "dirty": [l for l in status.splitlines() if l][:40], "recent": log.splitlines(),
            "branches": branches.splitlines()[:40], "unpushed": ahead}


def rollback_point(root, label):
    """A local tag at HEAD before migration; the working tree changes are listed (they are not part of it)."""
    h = head(root)
    if not h:
        return {"ok": False, "why": "no commit yet: no rollback point possible"}
    tag = "sg2-pre-migration-" + re.sub(r"[^A-Za-z0-9-]", "-", label)
    rc, _, err = git(root, "tag", "-a", "-m", "SESSION-GUARD: 2.0 rollback point before migration", tag, h)
    if rc and "already exists" not in err:
        return {"ok": False, "why": err}
    _, status, _ = git(root, "status", "--porcelain")
    return {"ok": True, "tag": tag, "commit": h, "uncommitted": [l for l in status.splitlines() if l]}


SKIP_FILES = ("store.lock", ".pyc")


def _force_files(root, rel):
    """Session Guard's files under .claude (a project may ignore .claude/): every file except lock, temp and
    Python cache files, which are never committed."""
    base = os.path.join(root, rel)
    if os.path.isfile(base):
        return [rel]
    found = []
    for folder, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for f in files:
            if not f.endswith(SKIP_FILES) and not f.endswith(".tmp"):
                found.append(os.path.relpath(os.path.join(folder, f), root).replace("\\", "/"))
    return found


def commit_paths(root, message, paths=SG_PATHS):
    present = [p for p in paths if os.path.exists(os.path.join(root, p))]
    _, status, _ = git(root, "status", "--porcelain", "--untracked-files=all", "--", *present)
    if not status:
        return {"committed": False, "commit": head(root)}
    git(root, "add", "-A", "--", *present)
    forced = [f for p in present if p.startswith(".claude") for f in _force_files(root, p)]
    if forced:
        git(root, "add", "-f", "--", *forced)
    rc, _, err = git(root, "commit", "-m", message, "--", *present)
    if rc:
        return {"committed": False, "error": err.splitlines()[-1] if err else "git commit failed"}
    return {"committed": True, "commit": head(root)}


def push_verified(root, target_branch=None):
    """Pushes the current branch and checks the remote has exactly this commit."""
    rem, br, h = remote(root), target_branch or branch(root), head(root)
    if not rem:
        return {"verified": False, "why": "no remote: kept locally only", "commit": h}
    if not br:
        return {"verified": False, "why": "detached HEAD: no branch to push", "commit": h}
    rc, _, err = git(root, "push", rem, "HEAD:refs/heads/%s" % br)
    if rc:
        return {"verified": False, "why": "push failed: %s" % (err.splitlines() or ["?"])[-1], "commit": h}
    rc, out, err = git(root, "ls-remote", rem, "refs/heads/%s" % br)
    remote_sha = out.split()[0] if rc == 0 and out else None
    return {"verified": remote_sha == h, "commit": h, "remote": rem, "branch": br, "remote_commit": remote_sha,
            "why": None if remote_sha == h else "remote shows %s, local is %s" % (remote_sha, h)}


# --- work of a task (hand-off ready) ---------------------------------------------------------------
# Cloud: a cloud checkout is gone with its session, so before a hand-off the unfinished work of each task is
# committed and pushed to its own side branch sg/<task-id>. Computer: the work stays in the folder (the next
# session opens the same folder); a side branch only when the user asks for it or approves it.
SIDE_PREFIX = "sg/"


def side_branch(tid):
    return SIDE_PREFIX + tid.lower()


def is_sg_path(rel):
    rel = rel.replace("\\", "/").strip('"')
    return any(rel == p or rel.startswith(p.rstrip("/") + "/") for p in SG_PATHS)


def changed_files(root):
    """Changed, new and deleted files outside Session Guard's own paths (the work that a hand-off must keep)."""
    rc, out, _ = git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all", strip=False)
    if rc:
        return []
    files, parts, i = [], out.split("\0"), 0
    while i < len(parts):
        entry = parts[i]
        i += 1
        if len(entry) < 4:
            continue
        code, rel = entry[:2], entry[3:]
        if "R" in code or "C" in code:
            i += 1  # -z puts the old name of a rename after the new one
        if not is_sg_path(rel):
            files.append(rel)
    return files


def select(files, paths):
    """The changed files under the given paths (a file or a folder)."""
    want = [p.replace("\\", "/").rstrip("/") for p in paths if p.strip()]
    return [f for f in files if any(f == w or f.startswith(w + "/") for w in want)]


def commit_work(root, files, message):
    """Commits exactly these files (new, changed or deleted), nothing else of the working tree."""
    if not files:
        return {"committed": False, "commit": head(root)}
    rc, _, err = git(root, "add", "-A", "--", *files)
    if rc:
        return {"committed": False, "error": (err.splitlines() or ["git add failed"])[-1]}
    rc, _, err = git(root, "commit", "-m", message, "--", *files)
    if rc:
        return {"committed": False, "error": (err.splitlines() or ["git commit failed"])[-1]}
    return {"committed": True, "commit": head(root)}


def remote_side_branches(root):
    """[(branch, commit)] of sg/* branches on the remote whose commit this checkout does not contain yet."""
    rem = remote(root)
    if not rem:
        return []
    rc, out, _ = git(root, "ls-remote", "--heads", rem, SIDE_PREFIX + "*", timeout=6, quiet=True)
    found = []
    for line in out.splitlines() if rc == 0 else []:
        sha, _, ref = line.partition("\t")
        name = ref.replace("refs/heads/", "", 1)
        if git(root, "merge-base", "--is-ancestor", sha, "HEAD")[0] != 0:
            found.append((name, sha))
    return found


def merged_side_branches(root):
    """sg/* branches on the remote already contained in the remote default branch (merged, safe to delete). Only
    commits this checkout has can be compared; others are left out."""
    rem = remote(root)
    if not rem:
        return []
    rc, out, _ = git(root, "ls-remote", rem, "HEAD", "refs/heads/" + SIDE_PREFIX + "*", timeout=6, quiet=True)
    if rc:
        return []
    refs = [l.split("\t") for l in out.splitlines() if "\t" in l]
    default = next((sha for sha, ref in refs if ref == "HEAD"), None)
    if not default:
        return []
    return [ref.replace("refs/heads/", "", 1) for sha, ref in refs if ref.startswith("refs/heads/")
            and sha != default and git(root, "merge-base", "--is-ancestor", sha, default)[0] == 0]


def conflicted(root):
    return [f for f in git(root, "diff", "--name-only", "--diff-filter=U")[1].splitlines() if f]


def pr_url(root):
    """The open pull request of the current branch, through the GitHub CLI when it is there; else None."""
    try:
        r = subprocess.run(["gh", "pr", "view", "--json", "url", "-q", ".url"], cwd=root, capture_output=True,
                           timeout=20, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return None
    out = r.stdout.decode("utf-8", "replace").strip()
    return out if r.returncode == 0 and out.startswith("http") else None


# --- session records in the cloud (user decision 2026-11-05) ---------------------------------------------------
# A cloud VM can be rebuilt with a fresh home folder, which holds Session Guard's session records (mode, locks,
# notices, message ids). In the cloud they are kept on the remote branch sg-state as well: written by plumbing
# commands into a temporary index (the work tree, the index and the current branch are never touched), pushed
# without force, retried when another session pushed first. Session start in the cloud reads them back.
STATE_BRANCH = "sg-state"
STATE_REF = "refs/sg/state"
STATE_AUTHOR = {"GIT_AUTHOR_NAME": "Session Guard", "GIT_AUTHOR_EMAIL": "session-guard@localhost",
                "GIT_COMMITTER_NAME": "Session Guard", "GIT_COMMITTER_EMAIL": "session-guard@localhost"}


def _run(root, args, env=None, data=None, timeout=30):
    try:
        r = subprocess.run(["git", "-C", root] + list(args), capture_output=True, timeout=timeout, input=data,
                           env=dict(os.environ, **QUIET, **(env or {})))
    except (OSError, subprocess.SubprocessError) as e:
        return 1, "", str(e)
    return r.returncode, r.stdout.decode("utf-8", "replace").strip(), r.stderr.decode("utf-8", "replace").strip()


def fetch_state(root, timeout=6):
    """The remote sg-state branch into STATE_REF; False when it does not exist or cannot be reached."""
    rem = remote(root)
    if not rem:
        return False
    rc, _, _ = _run(root, ["fetch", "-q", "--no-tags", rem, "+refs/heads/%s:%s" % (STATE_BRANCH, STATE_REF)],
                    timeout=timeout)
    return rc == 0


def read_state(root, folder="sessions"):
    """{file name: text} under folder/ of the fetched sg-state branch."""
    rc, out, _ = _run(root, ["ls-tree", "--name-only", "%s:%s" % (STATE_REF, folder)])
    found = {}
    for name in out.splitlines() if rc == 0 else []:
        rc2, text, _ = _run(root, ["show", "%s:%s/%s" % (STATE_REF, folder, name)])
        if rc2 == 0:
            found[name] = text
    return found


def write_state(root, files, message, tries=3):
    """Adds or replaces files ({path: text}) on the remote sg-state branch. Returns {"verified", "commit", "why"}."""
    rem = remote(root)
    if not rem:
        return {"verified": False, "why": "no remote"}
    why = None
    for _ in range(tries):
        parent = None
        if fetch_state(root, timeout=20):
            rc, out, _ = _run(root, ["rev-parse", "--verify", "-q", STATE_REF])
            parent = out if rc == 0 and out else None
        fd, index = tempfile.mkstemp(prefix="sg-state-index-")
        os.close(fd)
        os.remove(index)
        env = dict(STATE_AUTHOR, GIT_INDEX_FILE=index)
        try:
            if parent and _run(root, ["read-tree", parent], env=env)[0]:
                why = "read-tree failed"
                continue
            for path, text in sorted(files.items()):
                rc, sha, err = _run(root, ["hash-object", "-w", "--stdin"], env=env, data=text.encode("utf-8"))
                if rc or _run(root, ["update-index", "--add", "--cacheinfo", "100644,%s,%s" % (sha, path)],
                              env=env)[0]:
                    return {"verified": False, "why": "cannot store %s: %s" % (path, err)}
            rc, tree, err = _run(root, ["write-tree"], env=env)
            if rc:
                return {"verified": False, "why": "write-tree: " + err}
            rc, commit, err = _run(root, ["commit-tree", tree, "-m", message] + (["-p", parent] if parent else []),
                                   env=env)
            if rc:
                return {"verified": False, "why": "commit-tree: " + err}
            rc, _, err = _run(root, ["push", "-q", rem, "%s:refs/heads/%s" % (commit, STATE_BRANCH)], timeout=60)
            if rc == 0:
                rc, out, _ = _run(root, ["ls-remote", rem, "refs/heads/" + STATE_BRANCH])
                ok = rc == 0 and out.split()[:1] == [commit]
                return {"verified": ok, "commit": commit, "why": None if ok else "remote shows %s" % out[:12]}
            why = "push refused: " + ((err.splitlines() or ["?"])[-1])  # another session pushed first: again
        finally:
            if os.path.exists(index):
                os.remove(index)
    return {"verified": False, "why": why}
