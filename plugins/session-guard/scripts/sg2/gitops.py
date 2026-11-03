# SESSION-GUARD: 2.0 | component: sg2-gitops | version: 2.0.0
"""Git steps: rollback point, committing only Session Guard's own paths, push and push verification.

A push counts only when `git ls-remote` shows the remote branch at the local commit. Other changes of the user
are never staged or committed: commits name their paths (`git commit -- <paths>`).
"""
import os
import re
import subprocess

SG_PATHS = (".claude/session-guard2", "docs/STATUS.md", "docs/STATUS_ARCHIVE.md", "docs/TASK_BACKLOG.md",
            "docs/COMPLETED_TASKS.md", "CLAUDE.md", ".claude/settings.json")


def git(root, *args, timeout=120):
    try:
        r = subprocess.run(["git", "-C", root] + list(args), capture_output=True, timeout=timeout,
                           stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError) as e:
        return 1, "", str(e)
    return r.returncode, r.stdout.decode("utf-8", "replace").strip(), r.stderr.decode("utf-8", "replace").strip()


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
    rc, _, err = git(root, "tag", tag, h)
    if rc and "already exists" not in err:
        return {"ok": False, "why": err}
    _, status, _ = git(root, "status", "--porcelain")
    return {"ok": True, "tag": tag, "commit": h, "uncommitted": [l for l in status.splitlines() if l]}


def commit_paths(root, message, paths=SG_PATHS):
    present = [p for p in paths if os.path.exists(os.path.join(root, p))]
    _, status, _ = git(root, "status", "--porcelain", "--untracked-files=all", "--", *present)
    if not status:
        return {"committed": False, "commit": head(root)}
    git(root, "add", "-A", "--", *present)
    git(root, "add", "-f", "--", *[p for p in present if p.startswith(".claude")])
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


def pr_url(root):
    """The open pull request of the current branch, through the GitHub CLI when it is there; else None."""
    try:
        r = subprocess.run(["gh", "pr", "view", "--json", "url", "-q", ".url"], cwd=root, capture_output=True,
                           timeout=20, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return None
    out = r.stdout.decode("utf-8", "replace").strip()
    return out if r.returncode == 0 and out.startswith("http") else None
