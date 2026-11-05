# SESSION-GUARD: 2.0 | component: sg2-migrate | version: 2.0.0
"""Migration of projects to Session Guard 2.0, with a checkpoint that survives sessions.

The goal is each project's current, checked state, not a rebuild of every old session:
    discover  projects Claude Code knows (transcript folders), projects 1.x reviewed, and any folder named
              later (`--add`); a project first seen at a session start joins the list by itself
    survey    facts for the reading agent: git state, recent commits, instruction / status / hand-off files,
              skills, hooks, 1.x leftovers, old sessions (deterministic, no guessing)
    apply     from a plan Claude writes after reading (current tasks, ideas, contradictions): rollback point,
              PROJECT-ID, task store, new project files; the old STATUS goes to the archive (append only)
    verify    the result is checked; only then the project counts as protected by 2.0
    push      (user-approved) commit of Session Guard's own paths only, push, push verified
Old sessions are not edited: their conversations began before the project's migrated_at, so 2.0 locks them at
their next prompt (sessions.py "legacy"). Old ids are never reused: 2.0 ids are new; old session ids are listed
in the store for the record.
Token budget: each installation session's measured tokens (peak context + output) against 800k; at 90% a
safe stop (ASSUMPTION: per session, since a continuation is a new session). Then only "continue installation" (in session-guard-executive) continues; every other prompt is
blocked while the installation is paused.
"""
import glob
import json
import os
import re
import shutil

from . import core, gitops, legacy, measure, place, render, store

BUDGET = 800 * core.K
SAFE_AT = 0.9
STATES = ("discovered", "surveyed", "applied", "verified", "pushed", "failed", "skipped")
INSTRUCTION_FILES = ("CLAUDE.md", "CLAUDE.local.md", ".claude/CLAUDE.md", "AGENTS.md", "GEMINI.md", ".cursorrules",
                     ".windsurfrules", ".clinerules", ".github/copilot-instructions.md", "README.md", "README.tr.md",
                     "CONTEXT.md", "MEMORY.md")
STATUS_LIKE = re.compile(r"status|hand-?off|devir|todo|progress|notes|backlog|task|görev|gorev|roadmap|plan", re.I)
# A file that keeps tasks, whatever its name: checkboxes, or headings that name pending / finished work.
CHECK_OPEN = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+\[ \]", re.M)
CHECK_DONE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+\[[xX✓]\]", re.M)
TASK_HEADING = re.compile(r"^#{1,6}\s+.*(?:to-?do|backlog|pending|open|next|done|completed|finished|tasks?|ideas?|"
                          r"yapılacak|yapilacak|bekleyen|sıradaki|siradaki|tamamlan|biten|görev|gorev|fikir)", re.I | re.M)
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", "Library", "Temp", "obj", "bin",
             ".next", "session-guard2"}
SG_DOCS = ("docs/STATUS_ARCHIVE.md", "docs/TASK_BACKLOG.md", "docs/IDEA_TASKS.md", "docs/COMPLETED_TASKS.md")


def task_list_files(root, depth=3, limit=40):
    """Files that list tasks (open or finished), found by content, not by name. Their open items become tasks or
    ideas, their finished items lines of COMPLETED_TASKS.md, and the files are retired after the user's yes."""
    found, base_depth = [], root.rstrip("\\/").count(os.sep)
    for folder, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")] \
            if folder.count(os.sep) - base_depth < depth else []
        for n in sorted(files):
            if os.path.splitext(n)[1].lower() not in (".md", ".txt", ""):
                continue
            p = os.path.join(folder, n)
            rel = os.path.relpath(p, root).replace("\\", "/")
            if rel in SG_DOCS or os.path.getsize(p) > 300000:
                continue
            text = core.read_text(p) or ""
            opened, done = len(CHECK_OPEN.findall(text)), len(CHECK_DONE.findall(text))
            heads = [h.strip() for h in TASK_HEADING.findall(text)][:6]
            if opened or done or heads:
                found.append({"path": rel, "open_items": opened, "done_items": done, "task_headings": heads,
                              "lines": len(text.splitlines()), "modified": core.iso(os.path.getmtime(p))})
            if len(found) >= limit:
                return found
    return found


def _norm(line):
    return re.sub(r"[^\w ]+", " ", re.sub(r"^\s*(?:[-*+>]|\d+[.)]|\[[ xX]\])\s*", "", line)).lower().split()


def duplicates(root, rule_files, state_files, limit=40):
    """Lines of the instruction files that repeat (nearly word for word) a line of a status or task file: in 2.0 a
    rule is never also a task or a state line, so each pair is settled in the plan."""
    import difflib
    out = []
    states = []
    for rel in state_files:
        for line in (core.read_text(os.path.join(root, rel)) or "").splitlines():
            words = _norm(line)
            if len(" ".join(words)) >= 20:
                states.append((rel, line.strip(), " ".join(words)))
    for rel in rule_files:
        for line in (core.read_text(os.path.join(root, rel)) or "").splitlines():
            words = " ".join(_norm(line))
            if len(words) < 20:
                continue
            for srel, sline, swords in states:
                if srel != rel and difflib.SequenceMatcher(None, words, swords).ratio() >= 0.85:
                    out.append({"rule_file": rel, "rule_line": line.strip(), "state_file": srel, "state_line": sline})
                    break
            if len(out) >= limit:
                return out
    return out


def path():
    return os.path.join(core.state_dir(), "migration.json")


def lock_path():
    return os.path.join(core.state_dir(), "migration.lock")


def load():
    data = core.read_json(path(), None)
    if not isinstance(data, dict):
        data = {"signature": "SESSION-GUARD: 2.0", "phase": "idle", "started": None, "sessions": [],
                "budget": {"limit": BUDGET, "used": {}},
                "projects": {}, "log": []}
    return data


def change(fn):
    with core.FileLock(lock_path()):
        data = load()
        result = fn(data)
        core.write_json(path(), data)
        return result


def log(data, text):
    data["log"].append("%s %s" % (core.iso(), text))
    data["log"] = data["log"][-200:]


def used(data):
    return sum(data["budget"]["used"].values())


def phase():
    return load()["phase"]


# --- discovery ------------------------------------------------------------------------
def first_cwd(p):
    try:
        with open(p, encoding="utf-8", errors="replace") as fh:
            for _, line in zip(range(200), fh):
                try:
                    cwd = json.loads(line).get("cwd")
                except (ValueError, AttributeError):
                    continue
                if cwd:
                    return cwd
    except OSError:
        pass
    return None


def transcript_folder(root):
    """~/.claude/projects/<cwd with every non-alphanumeric character as '-'> (documented)."""
    return os.path.join(core.claude_dir(), "projects", re.sub(r"[^A-Za-z0-9]", "-", os.path.abspath(root)))


def known_projects():
    roots = {}
    for folder in glob.glob(os.path.join(core.claude_dir(), "projects", "*")):
        for p in sorted(glob.glob(os.path.join(folder, "*.jsonl")), key=os.path.getmtime, reverse=True)[:3]:
            cwd = first_cwd(p)
            if cwd:
                root = core.usable_root(cwd)
                if root:
                    root = core.main_checkout(root)
                    roots[core.key(root)] = root
                break
    v1 = core.read_json(os.path.join(core.v1_state_dir(), "config", "setup.json"), {}) or {}
    for k_ in (v1.get("projects") or {}):
        if os.path.isdir(k_) and core.usable_root(k_):
            roots.setdefault(core.key(k_), k_)
    return sorted(roots.values(), key=str.lower)


def discover(extra=()):
    found = known_projects() + [os.path.abspath(e) for e in extra if os.path.isdir(e)]

    def fn(data):
        added = []
        if data["phase"] == "idle":
            data["phase"], data["started"] = "running", core.iso()
        for root in found:
            k_ = core.key(root)
            if k_ not in data["projects"]:
                state = "verified" if store.is_project(root) else "discovered"
                data["projects"][k_] = {"path": root, "state": state, "project_id": None, "updated": core.iso()}
                added.append(root)
        log(data, "discover: %d new" % len(added))
        return added
    return change(fn)


def note_seen(root):
    """A project met at a session start that the migration does not know yet joins the list (found later)."""
    if not root or store.is_project(root):
        return False
    data = load()
    if data["phase"] == "idle" or core.key(root) in data["projects"]:
        return False

    def fn(d):
        d["projects"].setdefault(core.key(root), {"path": root, "state": "discovered", "project_id": None,
                                                  "updated": core.iso(), "found_later": True})
        log(d, "found later: %s" % root)
        return True
    return change(fn)


# --- survey ----------------------------------------------------------------------------
def _file_info(root, rel):
    p = core.exists_ci(root, rel)
    if not p or not os.path.isfile(p):
        return None
    text = core.read_text(p) or ""
    return {"path": os.path.relpath(p, root).replace("\\", "/"), "lines": len(text.splitlines()),
            "modified": core.iso(os.path.getmtime(p)), "head": text.splitlines()[:6]}


def legacy_sessions(root):
    out = []
    for p in sorted(glob.glob(os.path.join(transcript_folder(root), "*.jsonl"))):
        sid = os.path.basename(p)[:-6]
        r = measure.read_transcript(p)
        from . import sessions
        out.append({"claude_id": sid, "first": core.iso(r.first_at) if r.first_at else None,
                    "last": core.iso(os.path.getmtime(p)), "v1_lock": sessions.v1_lock(sid),
                    "pending_jobs": r.pending})
    return out


def survey(root):
    root = core.main_checkout(os.path.abspath(root))
    facts = {"path": root, "surveyed": core.iso(), "git": gitops.facts(root), "legacy_v1": legacy.project_report(root),
             "instruction_files": [], "status_like_files": [], "claude_dirs": {}, "sessions": legacy_sessions(root)}
    for rel in INSTRUCTION_FILES:
        info = _file_info(root, rel)
        if info:
            facts["instruction_files"].append(info)
    for folder in (root, os.path.join(root, "docs")):
        try:
            names = sorted(os.listdir(folder))
        except OSError:
            continue
        for n in names:
            if os.path.isfile(os.path.join(folder, n)) and os.path.splitext(n)[1].lower() in ("", ".md", ".txt") \
                    and STATUS_LIKE.search(n):
                info = _file_info(folder, n)
                info["path"] = os.path.relpath(os.path.join(folder, n), root).replace("\\", "/")
                facts["status_like_files"].append(info)
    facts["task_list_files"] = task_list_files(root)
    state_files = sorted({f["path"] for f in facts["status_like_files"]} | {f["path"] for f in facts["task_list_files"]})
    facts["duplicates"] = duplicates(root, [f["path"] for f in facts["instruction_files"]
                                            if not f["path"].lower().startswith("readme")], state_files)
    for sub in ("skills", "commands", "agents", "hooks", "scripts", "rules"):
        p = os.path.join(root, ".claude", sub)
        if os.path.isdir(p):
            facts["claude_dirs"][sub] = sorted(os.listdir(p))
    st = legacy.settings(os.path.join(root, ".claude", "settings.json"))
    facts["hooks"] = ["%s: %s" % c for c in legacy.hook_commands(st)]
    if facts["git"].get("git"):
        _, files, _ = gitops.git(root, "log", "-15", "--name-only", "--format=--%h %ad", "--date=short")
        facts["git"]["recent_files"] = files.splitlines()[:80]
    out = os.path.join(core.state_dir(), "migration", core.safe_name(core.key(root))[-60:] + ".survey.json")
    core.write_json(out, facts)

    def fn(data):
        rec = data["projects"].setdefault(core.key(root), {"path": root})
        rec.update(state="surveyed", survey=out, updated=core.iso())
        log(data, "survey: %s" % root)
    change(fn)
    return out, facts


# --- apply -----------------------------------------------------------------------------
PLAN_HELP = """Plan file (JSON), written by Claude after reading the survey and the files it names:
{"name": "...", "home": "computer" | "cloud",
 "tasks": [{"title": "...", "priority": "P1|P2|P3", "status": "open|in_progress|blocked",
            "done": ["..."], "pending": ["..."], "subtasks": [{"title": "...", "done": [], "pending": []}]}],
 "ideas": ["..."], "completed": ["one line per finished item found"],
 "queue": [{"title": "...", "priority": "P2", "pending": ["..."]}],
 "retire": ["files that ONLY listed tasks / status / hand-offs: deleted whole, no copy (content is in the plan)"],
 "remove_text": [{"file": "README.md", "text": "the exact task text to cut out of a file used for other things too"}],
 "not_carried": ["items left out because of the limits (listed in the archive)"],
 "claude_md_remove": ["exact CLAUDE.md lines that are state or tasks, not rules (or repeat a task / STATUS line)"],
 "rule_scripts": [{"rule": "the exact CLAUDE.md rule line", "script": "what a script or hook would check"}],
 "contradictions": ["..."], "uncertain": ["..."], "sources": ["files and commits the plan rests on"]}
At most 10 active tasks, 50 queued tasks and 50 ideas (rule_scripts not counted); the rest in "not_carried", shown
to the user. Every open item of the survey's task_list_files becomes a task, a subtask, a queued task or an idea,
every finished item a "completed" line (id PRE20-DONE-xxx; ideas get PRE20-IDEA-xxx, tasks are marked pre-2.0).
Then a file that only listed tasks goes into "retire" (deleted whole), task text inside other files into
"remove_text" (cut out), so no agent reads the old lists after the migration. A rule stays only in CLAUDE.md, a task or state line only in Session Guard's files: settle each
pair of the survey's "duplicates" in the plan."""


def _validate_plan(plan):
    if not isinstance(plan, dict) or not isinstance(plan.get("tasks", []), list):
        raise store.Refused("plan must be a JSON object with a tasks list. " + PLAN_HELP)
    if len(plan.get("tasks", [])) > store.MAX_NORMAL:
        raise store.Refused("plan has %d tasks; at most %d are active, put the rest in ideas" % (
            len(plan["tasks"]), store.MAX_NORMAL))
    for t in plan.get("tasks", []):
        if not str(t.get("title") or "").strip():
            raise store.Refused("every plan task needs a title")
    if plan.get("home", "computer") not in ("computer", "cloud"):
        raise store.Refused("home must be computer or cloud")
    for rel in list(plan.get("retire") or []) + [x.get("file") for x in plan.get("remove_text") or []
                                                  if isinstance(x, dict)]:
        r = str(rel or "").replace("\\", "/").lstrip("/")
        if not r or ".." in r.split("/") or r.lower() in ("claude.md", "docs/status.md") or r in SG_DOCS \
                or r.startswith(".claude/session-guard2"):
            raise store.Refused("cannot retire or edit %s (outside the project, CLAUDE.md (use claude_md_remove), or a "
                                "Session Guard file)" % rel)
    for x in plan.get("remove_text") or []:
        if not (isinstance(x, dict) and str(x.get("text") or "").strip()):
            raise store.Refused("remove_text entries are {\"file\": ..., \"text\": \"the exact text to cut\"}")
    if len(plan.get("queue") or []) > store.MAX_QUEUE or len(plan.get("ideas") or []) > store.MAX_IDEAS:
        raise store.Refused("at most %d queued tasks and %d ideas: put the rest in \"not_carried\" (they are listed "
                            "in the archive) and tell the user" % (store.MAX_QUEUE, store.MAX_IDEAS))
    for t in plan.get("queue") or []:
        if not str(t.get("title") or "").strip():
            raise store.Refused("every queued task needs a title")


def _retire(root, plan, bdir, report):
    """After apply. A file that only listed tasks is deleted whole (no copy: the user approved it by name, and its
    content now lives in Session Guard's files); task text inside another file is cut out of it; CLAUDE.md loses
    its state lines. Git projects: the rollback tag holds the committed versions; a project without git keeps a copy
    of the files that were only edited (outside the project, so no agent reads it by mistake)."""
    retired, edited, git = [], [], gitops.is_repo(root)

    def keep_copy(p):
        if not git:
            os.makedirs(bdir, exist_ok=True)
            shutil.copy2(p, os.path.join(bdir, "edited__" + os.path.relpath(p, root).replace(os.sep, "__")))
    for rel in plan.get("retire") or []:
        p = core.exists_ci(root, str(rel).replace("\\", "/"))
        if not p or not os.path.isfile(p):
            report["warnings"].append("retire: %s not found" % rel)
            continue
        os.remove(p)
        retired.append(os.path.relpath(p, root).replace("\\", "/"))
    for x in plan.get("remove_text") or []:
        p = core.exists_ci(root, str(x["file"]).replace("\\", "/"))
        text = core.read_text(p) if p else None
        cut = str(x["text"]).replace("\r\n", "\n").strip("\n")
        if not text or text.replace("\r\n", "\n").count(cut) != 1:
            report["warnings"].append("remove_text: the text was not found exactly once in %s; nothing cut" % x["file"])
            continue
        keep_copy(p)
        core.write_text(p, render.tidy(text.replace("\r\n", "\n").replace(cut, "", 1)))
        edited.append(os.path.relpath(p, root).replace("\\", "/"))
    lines = [str(l).strip() for l in plan.get("claude_md_remove") or [] if str(l).strip()]
    cm = core.exists_ci(root, "CLAUDE.md")
    if lines and cm:
        keep_copy(cm)
        text = core.read_text(cm)
        kept, removed = [], []
        for line in text.splitlines(keepends=True):
            if line.strip() in lines and line.strip() not in removed:
                removed.append(line.strip())
                continue
            kept.append(line)
        core.write_text(cm, render.tidy("".join(kept)))
        report["warnings"] += ["CLAUDE.md line not found, kept nothing: %s" % l for l in lines if l not in removed]
        report["claude_md_removed"] = removed
    report["retired"], report["edited"] = retired, edited


def backup_dir(root):
    return os.path.join(core.state_dir(), "backups", core.safe_name(core.key(root))[-60:])


def apply(root, plan, migrator_session):
    root = core.main_checkout(os.path.abspath(root))
    _validate_plan(plan)
    rec = load()["projects"].get(core.key(root)) or {}
    if rec.get("state") not in ("surveyed",):
        raise store.Refused("survey the project first (`sg migrate survey`); state now: %s" % rec.get("state"))
    if store.is_project(root):
        raise store.Refused("already a 2.0 project")
    report = {"rollback": None, "warnings": []}
    if gitops.is_repo(root):
        rb = gitops.rollback_point(root, core.today())
        report["rollback"] = rb
        if not rb.get("ok"):
            raise store.Refused("no rollback point: " + rb.get("why", "?"))
        if rb.get("uncommitted"):
            report["warnings"].append("uncommitted changes exist; they are not in the rollback tag and are left alone")
    else:
        report["warnings"].append("NOT A GIT REPOSITORY: no git rollback guarantee; only a file copy is kept")
    # A copy of every file this step changes, git or not (an uncommitted STATUS is not in the tag).
    bdir = backup_dir(root)
    for rel in ("docs/STATUS.md",) + SG_DOCS:
        p = core.exists_ci(root, rel)
        if p:
            os.makedirs(bdir, exist_ok=True)
            shutil.copy2(p, os.path.join(bdir, rel.replace("/", "__")))
    report["backup"] = bdir if os.path.isdir(bdir) else None

    old_status = core.read_text(core.exists_ci(root, "docs/STATUS.md") or "")
    data, _ = store.init(root, name=plan.get("name") or os.path.basename(root), home=plan.get("home", "computer"),
                         migrated=False, migration_session=migrator_session)
    if old_status:
        render.append_archive(root, "Pre-2.0 STATUS snapshot (Session Guard 1.x)",
                              ["    " + l for l in old_status.splitlines()] or ["(empty)"])
        p = core.exists_ci(root, "docs/STATUS.md")
        os.remove(p)  # rebuilt from the store below; the old text is in the archive and the backup

    def fn(d):
        mig = store.new_task(d, migrator_session, "Session Guard 2.0 migration", kind="migration", priority="P1")
        mig["done"].append("survey, plan, rollback point")
        mig["pending"].append("verify; push (user-approved)")
        mig["owner"] = None
        origin = "pre-2.0 (%s)" % "; ".join(plan.get("sources") or ["migration"])[:200]
        for t in plan.get("queue") or []:
            q = store.new_task(d, migrator_session, t["title"].strip(), priority=t.get("priority", "P2"),
                               approval="migration plan (user-approved migration)", queue=True, origin=origin)
            q["done"], q["pending"] = list(t.get("done") or []), list(t.get("pending") or [])
        for t in plan.get("tasks", []):
            nt = store.new_task(d, migrator_session, t["title"].strip(), priority=t.get("priority", "P2"),
                                approval="migration plan (user-approved migration)", origin=origin)
            nt["status"] = t.get("status", "open") if t.get("status") in store.ACTIVE else "open"
            nt["done"], nt["pending"] = list(t.get("done") or []), list(t.get("pending") or [])
            nt["owner"], nt["owners"][0]["how"] = None, "imported by migration (claimable)"
            for s in t.get("subtasks") or []:
                st = store.new_task(d, migrator_session, s["title"].strip(), parent=nt["id"])
                st["done"], st["pending"] = list(s.get("done") or []), list(s.get("pending") or [])
                st["owner"], st["owners"][0]["how"] = None, "imported by migration (claimable)"
        for idea in plan.get("ideas") or []:
            store.add_idea(d, migrator_session, str(idea), source="migration (pre-2.0)", force="migration plan",
                           prefix="PRE20-IDEA")
        d["legacy_sessions"] = [s["claude_id"] for s in legacy_sessions(root)]
        d["migrated_at"] = core.now()
        if migrator_session not in d["migration_sessions"]:
            d["migration_sessions"].append(migrator_session)
        return mig["id"]
    mig_id = store.mutate(root, fn)
    if plan.get("home", "computer") == "computer" and place.ensure_home_guard(root):
        report["warnings"].append("computer guard added to .claude/settings.json (blocks cloud sessions once pushed)")
    pid = store.load(root)["project_id"]
    for n, line in enumerate(plan.get("completed") or [], 1):
        core.append_text(render.doc(root, "COMPLETED_TASKS.md"), "- %s %s %s PRE20-DONE-%03d · %s · done before "
                         "Session Guard 2.0\n" % (core.today(), render.SIGN, pid, n, line))
    _retire(root, plan, bdir, report)
    from . import rules as rl

    def reviewed(d):
        rl.mark_reviewed(root, d, migrator_session)
        by_text = {r["text"]: rid for rid, r in d["rules"].items()}
        for idea in plan.get("rule_scripts") or []:
            rule = by_text.get(str(idea.get("rule") or "").strip()) if isinstance(idea, dict) else None
            text = idea.get("script") if isinstance(idea, dict) else idea
            store.add_idea(d, migrator_session, "Script instead of a rule: %s" % text, source="rule-review",
                           rule=rule, force="migration plan")
    store.mutate(root, reviewed)
    notes = ["Plan sources: " + "; ".join(plan.get("sources") or ["-"])]
    notes += ["Contradiction: " + c for c in plan.get("contradictions") or []]
    notes += ["Uncertain: " + c for c in plan.get("uncertain") or []]
    notes += ["Warning: " + w for w in report["warnings"]]
    if report["rollback"]:
        notes.append("Rollback: git tag %s at %s (undo: `git reset --hard %s` after saving later work; then delete "
                     ".claude/session-guard2/)" % (report["rollback"]["tag"], report["rollback"]["commit"][:12],
                                                   report["rollback"]["tag"]))
    if report["backup"]:
        notes.append("File copies before migration: " + report["backup"])
    notes += ["Retired (only listed tasks; content moved into Session Guard's files): " + r for r in report["retired"]]
    notes += ["Task text cut out (moved into Session Guard's files): " + r for r in report["edited"]]
    notes += ["Not carried over (limit %d queued tasks / %d ideas): %s" % (store.MAX_QUEUE, store.MAX_IDEAS, c)
              for c in plan.get("not_carried") or []]
    notes += ["Removed from CLAUDE.md (state, not a rule): " + r for r in report.get("claude_md_removed") or []]
    render.append_archive(root, "Migration to Session Guard 2.0 (%s)" % store.load(root)["project_id"], notes)
    pid = store.load(root)["project_id"]

    def upd(d):
        d["projects"][core.key(root)].update(state="applied", project_id=pid, migration_task=mig_id,
                                             rollback=report["rollback"], backup=report["backup"],
                                             retired=report["retired"] + report["edited"],
                                             warnings=report["warnings"], updated=core.iso())
        log(d, "apply: %s -> %s" % (root, pid))
    change(upd)
    return pid, report


# --- verify / push ------------------------------------------------------------------------
def verify(root):
    root = core.main_checkout(os.path.abspath(root))
    problems = []
    data = store.load(root)
    rec = load()["projects"].get(core.key(root)) or {}
    if not data:
        return ["no store"]
    if not core.PROJECT_ID.match(data["project_id"]):
        problems.append("bad PROJECT-ID")
    if not data.get("migrated_at"):
        problems.append("migrated_at not set: old sessions would not be locked")
    if not render.block_intact(root):
        problems.append("STATUS.md block missing or edited")
    try:
        store.validate(data)
    except store.Refused as e:
        problems.append(str(e))
    if rec.get("rollback") is None and gitops.is_repo(root):
        problems.append("git project without rollback point")
    listed = set(data.get("legacy_sessions") or [])
    for s in legacy_sessions(root):
        if s["claude_id"] not in listed and (s["first"] and core.to_time(s["first"]) < data["migrated_at"]):
            problems.append("old session not recorded: " + s["claude_id"])
    if legacy.project_report(root)["v1_cloud_copy"]:
        problems.append("1.x cloud copy still in .claude/session-guard/ (remove it with the user's approval)")
    if not problems:
        mig = rec.get("migration_task")
        if mig and data["tasks"].get(mig, {}).get("status") in store.ACTIVE:
            def fin(d):
                t = d["tasks"][mig]
                t["owner"] = d["migration_sessions"][0]
                store.close(d, t["owner"], mig, summary="verified")
            store.mutate(root, fin)

        def upd(d):
            d["projects"][core.key(root)].update(state="verified", updated=core.iso())
            log(d, "verify ok: %s" % root)
        change(upd)
    return problems


def push(root, approval):
    root = core.main_checkout(os.path.abspath(root))
    if not (approval or "").strip():
        raise store.Refused("pushing needs the user's approval: --approval \"<the user's words>\"")
    rec = load()["projects"].get(core.key(root)) or {}
    if rec.get("state") != "verified":
        raise store.Refused("verify first (state: %s)" % rec.get("state"))
    if not gitops.is_repo(root):
        return {"verified": False, "why": "not a git repository: nothing to push (no remote rollback guarantee)"}
    data = store.load(root)
    gone = [r for r in rec.get("retired") or [] if gitops.git(root, "ls-files", "--error-unmatch", "--", r)[0] == 0
            or os.path.exists(os.path.join(root, r))]
    if gone:
        c = gitops.commit_work(root, gone, "Session Guard 2.0: retire old task files of %s (moved into Session "
                               "Guard's files)\n\nSESSION-GUARD: 2.0" % data["name"])
        if c.get("error"):
            return {"verified": False, "why": c["error"]}
    c = gitops.commit_paths(root, "Session Guard 2.0: migrate %s (%s)\n\nSESSION-GUARD: 2.0" % (
        data["name"], data["project_id"]))
    if c.get("error"):
        return {"verified": False, "why": c["error"]}
    result = gitops.push_verified(root)
    tag = (rec.get("rollback") or {}).get("tag")
    if result.get("verified") and tag:
        gitops.git(root, "push", result["remote"], "refs/tags/%s" % tag)

    def upd(d):
        r = d["projects"][core.key(root)]
        r.update(push=result, updated=core.iso())
        if result.get("verified"):
            r["state"] = "pushed"
        log(d, "push %s: %s" % (root, "verified" if result.get("verified") else result.get("why")))
    change(upd)
    return result


# --- budget, pause, continue --------------------------------------------------------------------
def add_session(claude_id):
    def fn(d):
        if claude_id and claude_id not in d["sessions"]:
            d["sessions"].append(claude_id)
    change(fn)


def measure_session(claude_id, reading):
    """Records a migration session's measured tokens; returns (this session's use, limit). ASSUMPTION: the 800k
    budget is per installation session; "continue installation" opens a new session with its own budget."""
    def fn(d):
        if claude_id in d["sessions"]:
            d["budget"]["used"][claude_id] = reading.peak + reading.output
        return d["budget"]["used"].get(claude_id, 0), d["budget"]["limit"]
    return change(fn)


def pause(reason):
    def fn(d):
        d["phase"] = "paused"
        d["paused"] = {"at": core.iso(), "reason": reason,
                       "left": [p["path"] for p in d["projects"].values() if p["state"] not in ("verified", "pushed",
                                                                                                "skipped")]}
        log(d, "pause: " + reason)
        return d["paused"]
    return change(fn)


def resume(claude_id):
    def fn(d):
        if d["phase"] != "paused":
            return None
        d["phase"] = "running"
        if claude_id not in d["sessions"]:
            d["sessions"].append(claude_id)
        log(d, "continue installation in %s" % claude_id)
        return d.get("paused")
    return change(fn)


def finish():
    def fn(d):
        left = [p["path"] for p in d["projects"].values() if p["state"] not in ("verified", "pushed", "skipped")]
        if left:
            raise store.Refused("projects not finished: " + ", ".join(left))
        d["phase"] = "done"
        log(d, "migration done")
    change(fn)


def skip(root, reason):
    def fn(d):
        r = d["projects"].setdefault(core.key(root), {"path": root})
        r.update(state="skipped", reason=reason, updated=core.iso())
        log(d, "skip %s: %s" % (root, reason))
    change(fn)


def summary():
    d = load()
    lines = ["phase: %s · tokens used by installation sessions %dk (limit %dk per session)" % (
        d["phase"], used(d) // 1000, d["budget"]["limit"] // 1000)]
    for p in sorted(d["projects"].values(), key=lambda p: p["path"].lower()):
        lines.append("  %-9s %s%s%s" % (p["state"], p["path"], " " + p["project_id"] if p.get("project_id") else "",
                                         " (found later)" if p.get("found_later") else ""))
    return "\n".join(lines)
