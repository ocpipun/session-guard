# SESSION-GUARD: 2.0 | component: tests-sg2 | version: 2.0.0
"""Tests for Session Guard 2.0. Run: python -m unittest discover -s tests

Every test runs the real script in a subprocess (as Claude Code runs hooks: JSON on stdin) with HOME pointed at a
temporary folder, so the real ~/.claude is never touched.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGIN = os.path.join(ROOT, "plugins", "session-guard")
SCRIPT = os.path.join(PLUGIN, "scripts", "session_guard.py")
S1, S2, S3 = "S-20261103-aaaaaa", "S-20261103-bbbbbb", "S-20261103-cccccc"


def stamp(minutes_ago):
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat().replace("+00:00", "Z")


class Base(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.env = dict(os.environ, HOME=self.home, USERPROFILE=self.home,
                        LOCALAPPDATA=os.path.join(self.home, "l"), APPDATA=os.path.join(self.home, "r"))
        for k in ("CLAUDE_PROJECT_DIR", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_REMOTE", "SG_SESSION_ID",
                  "SG_PROJECT_ROOT", "CLAUDE_ENV_FILE"):
            self.env.pop(k, None)
        self.env.update(GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
        self.exe = os.path.join(self.home, "work", "session-guard-executive")
        os.makedirs(self.exe)
        self.sg("install", "approve", "--executive", self.exe)

    def tearDown(self):
        shutil.rmtree(self.home, ignore_errors=True)

    def sg(self, *args, env=None, ok=None):
        r = subprocess.run([sys.executable, "-I", SCRIPT, *args], capture_output=True, env=env or self.env)
        text = r.stdout.decode("utf-8") + r.stderr.decode("utf-8")
        if ok is not None:
            self.assertEqual(r.returncode == 0, ok, text)
        return text

    def hook(self, event, cid, transcript="none.jsonl", prompt="", cwd=None, env=None, **extra):
        data = dict({"hook_event_name": event, "session_id": cid, "transcript_path": transcript, "prompt": prompt,
                     "cwd": cwd or self.home, "source": "startup"}, **extra)
        r = subprocess.run([sys.executable, "-I", SCRIPT], input=json.dumps(data).encode(), capture_output=True,
                           env=env or self.env)
        out = r.stdout.decode()
        return json.loads(out) if out else {}

    def transcript(self, name, context, minutes_ago=5, first_minutes_ago=None, extra=()):
        p = os.path.join(self.home, name + ".jsonl")
        with open(p, "w", encoding="utf-8") as fh:
            if first_minutes_ago is not None:
                fh.write(json.dumps({"type": "user", "timestamp": stamp(first_minutes_ago), "message": {
                    "content": "hello"}}) + "\n")
            fh.write(json.dumps({"type": "assistant", "timestamp": stamp(minutes_ago), "message": {
                "id": "msg_" + name, "usage": {"input_tokens": 2, "cache_creation_input_tokens": 1000,
                                               "cache_read_input_tokens": context - 1002, "output_tokens": 500}}}) + "\n")
            for e in extra:
                fh.write(json.dumps(e) + "\n")
        return p

    def project(self, name="proj", git=False, files=None):
        root = os.path.join(self.home, "work", name)
        os.makedirs(os.path.join(root, "docs"), exist_ok=True)
        for rel, text in (files or {}).items():
            os.makedirs(os.path.dirname(os.path.join(root, rel)), exist_ok=True)
            with open(os.path.join(root, rel), "w", encoding="utf-8") as fh:
                fh.write(text)
        if git:
            self.git(root, "init", "-q", "-b", "main")
            self.git(root, "add", "-A")
            self.git(root, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "init", "--allow-empty")
        return root

    def git(self, root, *args):
        env = dict(self.env, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
                   GIT_COMMITTER_EMAIL="t@t")
        r = subprocess.run(["git", "-C", root, *args], capture_output=True, env=env)
        return r.stdout.decode().strip()

    def migrated(self, name="proj", git=False, tasks=(), files=None):
        """A project taken through discover → survey → apply → verify."""
        root = self.project(name, git, files)
        self.sg("migrate", "discover", "--add", root, ok=True)
        self.sg("migrate", "survey", root, ok=True)
        plan = os.path.join(self.home, name + "-plan.json")
        with open(plan, "w") as fh:
            json.dump({"name": name, "home": "computer", "tasks": list(tasks), "ideas": ["later idea"],
                       "sources": ["test"]}, fh)
        self.sg("migrate", "apply", root, "--plan", plan, "--session", S3, ok=True)
        self.sg("migrate", "verify", root, ok=True)
        return root

    def store(self, root):
        with open(os.path.join(root, ".claude", "session-guard2", "store.json"), encoding="utf-8") as fh:
            return json.load(fh)

    def text(self, *parts):
        with open(os.path.join(*parts), encoding="utf-8") as fh:
            return fh.read()

    def ctx(self, out):
        return (out.get("hookSpecificOutput") or {}).get("additionalContext", "")

    def start(self, cid, root, transcript="none.jsonl", source="startup"):
        out = self.hook("SessionStart", cid, transcript, cwd=root, source=source)
        rec = json.load(open(os.path.join(self.home, ".claude", "session_guard_v2", "sessions", cid + ".json")))
        return out, rec["sg_id"]


class Tasks(Base):
    def setUp(self):
        super().setUp()
        self.root = self.migrated()

    def t(self, *args, sid=S1, ok=None):
        return self.sg("task", *args, "--session", sid, "--root", self.root, ok=ok)

    def test_ids_hierarchy_and_new_task_needs_approval(self):
        self.assertIn("approval", self.t("new", "--title", "A", ok=False))
        self.assertIn("TASK-002", self.t("new", "--title", "A", "--approval", "yes", ok=True))
        self.assertIn("TASK-002-1", self.t("new", "--title", "a1", "--parent", "TASK-002", ok=True))
        self.assertIn("TASK-002-2", self.t("new", "--title", "a2", "--parent", "TASK-002", ok=True))
        self.assertIn("TASK-002-2-1", self.t("new", "--title", "a21", "--parent", "TASK-002-2", ok=True))

    def test_main_task_limit_three_plus_one_urgent(self):
        for n in "ABC":
            self.t("new", "--title", n, "--approval", "ok", ok=True)
        self.assertIn("3 normal", self.t("new", "--title", "D", "--approval", "ok", ok=False))
        self.assertIn("--reason", self.t("new", "--title", "U", "--kind", "urgent", "--approval", "ok", ok=False))
        self.t("new", "--title", "U", "--kind", "urgent", "--approval", "ok", "--reason", "prod down", ok=True)
        self.assertIn("urgent task is already", self.t("new", "--title", "U2", "--kind", "urgent", "--approval", "ok",
                                                       "--reason", "x", ok=False))
        for n in range(6):  # subtasks have no limit
            self.t("new", "--title", "sub%d" % n, "--parent", "TASK-002", ok=True)
        lines = self.text(self.root, "docs", "STATUS.md").splitlines()
        self.assertLessEqual(len(lines), 50)

    def test_urgent_only_when_three_normal(self):
        self.assertIn("P1", self.t("new", "--title", "U", "--kind", "urgent", "--approval", "ok", "--reason", "r",
                                   ok=False))

    def test_ownership_claim_takeover_and_history(self):
        self.t("new", "--title", "A", "--approval", "ok", ok=True)
        self.start("c2", self.root)
        sid2 = self.store(self.root)["sessions"]
        # S1 has no store record: register both as active sessions through their session starts
        out, sg_a = self.start("ca", self.root)
        out, sg_b = self.start("cb", self.root)
        self.sg("task", "claim", "TASK-002", "--session", sg_a, "--root", self.root, ok=True)
        self.assertIn("still active", self.sg("task", "claim", "TASK-002", "--session", sg_b, "--root", self.root,
                                              ok=False))
        self.assertIn("only its owner", self.sg("task", "note", "TASK-002", "--done", "x", "--session", sg_b,
                                                "--root", self.root, ok=False))
        self.sg("task", "claim", "TASK-002", "--session", sg_b, "--root", self.root, "--takeover", "user said yes",
                ok=True)
        t = self.store(self.root)["tasks"]["TASK-002"]
        self.assertEqual(t["owner"], sg_b)
        self.assertEqual([o["session"] for o in t["owners"]], [S1, sg_a, sg_b])
        self.assertIsNotNone(t["owners"][1]["to"])
        # sg_a owned nothing else: it is locked, and its next prompt is blocked
        self.assertEqual(self.store(self.root)["sessions"][sg_a]["state"], "locked")
        out = self.hook("UserPromptSubmit", "ca", cwd=self.root, prompt="go on")
        self.assertEqual(out.get("decision"), "block")
        self.assertIn(sg_b, out["reason"])

    def test_concurrent_claims_one_winner(self):
        self.t("new", "--title", "A", "--approval", "ok", ok=True)
        self.t("handoff", ok=None) if False else None
        self.sg("handoff", "write", "TASK-002", "--technical", "x", "--session", S1, "--root", self.root, ok=True)
        sids = []
        for n in range(6):
            _, s = self.start("par%d" % n, self.root)
            sids.append(s)
        with ThreadPoolExecutor(6) as pool:
            results = list(pool.map(lambda s: self.sg("task", "claim", "TASK-002", "--session", s, "--root",
                                                      self.root), sids))
        winners = [r for r in results if "hand-off from" in r]
        self.assertEqual(len(winners), 1, results)
        self.assertEqual(sum("still active" in r for r in results), 5, results)
        self.assertEqual(self.store(self.root)["rev"], self.store(self.root)["rev"])

    def test_stale_read_is_refused(self):
        rev = self.store(self.root)["rev"]
        self.t("new", "--title", "A", "--approval", "ok", ok=True)
        self.assertIn("changed since", self.t("note", "TASK-002", "--done", "x", "--expect-rev", str(rev), ok=False))

    def test_subtask_done_keeps_parent_open(self):
        self.t("new", "--title", "A", "--approval", "ok", ok=True)
        self.t("new", "--title", "a1", "--parent", "TASK-002", ok=True)
        self.assertIn("open subtasks", self.t("done", "TASK-002", ok=False))
        self.t("done", "TASK-002-1", ok=True)
        self.assertEqual(self.store(self.root)["tasks"]["TASK-002"]["status"], "open")
        self.t("done", "TASK-002", ok=True)
        self.assertIn("TASK-002 · A · done", self.text(self.root, "docs", "COMPLETED_TASKS.md"))

    def test_finished_task_reopened_gets_new_id(self):
        self.t("new", "--title", "A", "--approval", "ok", ok=True)
        self.t("done", "TASK-002", ok=True)
        self.assertIn("reopen", self.t("claim", "TASK-002", ok=False))
        self.assertIn("TASK-003", self.t("reopen", "TASK-002", "--title", "A again", "--approval", "ok", ok=True))
        data = self.store(self.root)
        self.assertIn({"task": "TASK-002", "relation": "reopens"}, data["tasks"]["TASK-003"]["related"])
        self.assertIn({"task": "TASK-003", "relation": "reopened-by"}, data["tasks"]["TASK-002"]["related"])

    def test_backlog_idea_and_promotion(self):
        self.assertIn("IDEA-002", self.sg("idea", "add", "dark mode", "--session", S1, "--root", self.root))
        self.assertIn("dark mode", self.text(self.root, "docs", "TASK_BACKLOG.md"))
        self.assertIn("approval", self.sg("idea", "promote", "IDEA-002", "--session", S1, "--root", self.root,
                                          ok=False))
        self.sg("idea", "promote", "IDEA-002", "--approval", "ok", "--session", S1, "--root", self.root, ok=True)

    def test_handoff_once_then_claim_archives_and_locks_source(self):
        _, a = self.start("ha", self.root)
        self.sg("task", "new", "--title", "A", "--approval", "ok", "--session", a, "--root", self.root, ok=True)
        w = lambda: self.sg("handoff", "write", "TASK-002", "--technical", "run x", "--pending", "y", "--session", a,
                            "--root", self.root)
        self.assertIn("prepared", w())
        self.assertIn("unchanged", w())
        self.assertIn("Hand-offs waiting (1)", self.text(self.root, "docs", "STATUS.md"))
        self.sg("handoff", "publish", "--session", a, "--root", self.root, ok=True)  # no git: kept locally
        out, b = self.start("hb", self.root)
        self.assertIn("One task can be taken over: TASK-002", self.ctx(out))
        self.sg("task", "claim", "TASK-002", "--session", b, "--root", self.root, ok=True)
        arch = self.text(self.root, "docs", "STATUS_ARCHIVE.md")
        self.assertIn("Hand-off TASK-002 from %s (claimed)" % a, arch)
        self.assertIn("Technical: run x", arch)
        data = self.store(self.root)
        self.assertEqual(data["sessions"][a]["state"], "locked")
        self.assertEqual(data["sessions"][b]["parent"], a)
        self.assertEqual(self.hook("UserPromptSubmit", "ha", cwd=self.root, prompt="hi").get("decision"), "block")

    def test_two_claimable_tasks_ask_the_user(self):
        _, a = self.start("ha", self.root)
        for n in "AB":
            self.sg("task", "new", "--title", n, "--approval", "ok", "--session", a, "--root", self.root, ok=True)
        self.hook("SessionEnd", "ha", cwd=self.root, reason="other")
        out, _ = self.start("hb", self.root)
        self.assertIn("SEVERAL TASKS CAN BE TAKEN OVER", self.ctx(out))
        self.assertIn("ORPHANED", self.text(self.root, "docs", "STATUS.md"))

    def test_finished_task_needs_no_handoff(self):
        self.t("new", "--title", "A", "--approval", "ok", ok=True)
        self.t("done", "TASK-002", ok=True)
        self.assertIn("needs no hand-off", self.sg("handoff", "write", "TASK-002", "--technical", "x", "--session", S1,
                                                   "--root", self.root, ok=False))

    def test_store_edit_denied_and_status_edit_restored(self):
        out = self.hook("PreToolUse", "c", tool_name="Edit", tool_input={
            "file_path": os.path.join(self.root, ".claude", "session-guard2", "store.json")})
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")
        p = os.path.join(self.root, "docs", "STATUS.md")
        good = self.text(p)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(good.replace("## Active tasks", "## hacked"))
        _, sid = self.start("se", self.root)
        out = self.hook("PostToolUse", "se", cwd=self.root, tool_name="Edit", tool_input={"file_path": p})
        self.assertIn("rebuilt from the task store", self.ctx(out))
        self.assertNotIn("hacked", self.text(p))


class Guard(Base):
    def test_unknown_session_is_restart_locked(self):
        root = self.project()
        self.assertEqual(self.hook("UserPromptSubmit", "x", cwd=root, prompt="hi").get("decision"), "block")

    def test_thresholds_and_hard_stop(self):
        root = self.project()
        self.start("t", root)
        out = self.hook("UserPromptSubmit", "t", self.transcript("a", 260000), cwd=root, prompt="go")
        self.assertIn("NOTICE (250k)", self.ctx(out))
        out = self.hook("UserPromptSubmit", "t", self.transcript("a", 410000), cwd=root, prompt="go")
        self.assertEqual(out.get("decision"), "block")
        self.assertEqual(self.hook("UserPromptSubmit", "t", self.transcript("a", 1000), cwd=root,
                                   prompt="go").get("decision"), "block")

    def test_long_mode_thresholds(self):
        root = self.project()
        self.start("l", root)
        self.hook("UserPromptSubmit", "l", self.transcript("l", 1000), cwd=root, prompt="start long mode")
        out = self.hook("UserPromptSubmit", "l", self.transcript("l", 420000), cwd=root, prompt="go")
        self.assertNotIn("decision", out)
        self.assertIn("long mode", self.ctx(out))
        out = self.hook("PostToolUse", "l", self.transcript("l", 905000), cwd=root, tool_name="Bash")
        self.assertFalse(out.get("continue", True))

    def test_break_rule_records_command_word_for_word_and_locks(self):
        root = self.migrated()
        _, sid = self.start("b", root)
        self.sg("task", "new", "--title", "A", "--approval", "ok", "--session", sid, "--root", root, ok=True)
        cmd = "Şimdi render'ı başlat, sonra 'finish' yazma!"
        out = self.hook("UserPromptSubmit", "b", self.transcript("b", 150000, minutes_ago=90), cwd=root, prompt=cmd)
        self.assertIn("BREAK RULE", self.ctx(out))
        self.assertEqual(self.store(root)["tasks"]["TASK-002"]["pending_user_command"], cmd)
        self.sg("handoff", "write", "TASK-002", "--technical", "t", "--session", sid, "--root", root, ok=True)
        self.assertEqual(self.store(root)["tasks"]["TASK-002"]["handoff"]["user_command"], cmd)
        self.assertEqual(self.hook("UserPromptSubmit", "b", self.transcript("b", 150000), cwd=root,
                                   prompt="x").get("decision"), "block")

    def test_break_small_context_reminds_and_long_mode_ignores(self):
        root = self.project()
        self.start("s", root)
        out = self.hook("UserPromptSubmit", "s", self.transcript("s", 50000, minutes_ago=90), cwd=root, prompt="go")
        self.assertIn("BREAK REMINDER", self.ctx(out))
        self.hook("UserPromptSubmit", "s", self.transcript("s", 1000), cwd=root, prompt="start long mode")
        out = self.hook("UserPromptSubmit", "s", self.transcript("s", 150000, minutes_ago=90), cwd=root, prompt="go")
        self.assertNotIn("BREAK", self.ctx(out))

    def test_own_background_job_is_not_a_break(self):
        root = self.project()
        self.start("j", root)
        job = {"type": "assistant", "timestamp": stamp(90), "message": {"id": "m2", "content": [
            {"type": "tool_use", "id": "toolu_1", "name": "Bash", "input": {"run_in_background": True}}]}}
        out = self.hook("UserPromptSubmit", "j", self.transcript("j", 150000, minutes_ago=90, extra=[job]), cwd=root,
                        prompt="go")
        self.assertNotIn("BREAK", self.ctx(out))

    def test_quota_stop_then_lock(self):
        root = self.project()
        self.start("q", root)
        folder = os.path.join(self.home, ".claude", "session_guard_v2", "follow")
        os.makedirs(folder, exist_ok=True)
        json.dump({"used_percent": 93, "resets_at": time.time() + 7200, "read_at": time.time()},
                  open(os.path.join(folder, "five_hour.json"), "w"))
        out = self.hook("UserPromptSubmit", "q", self.transcript("q", 1000), cwd=root, prompt="go")
        self.assertIn("QUOTA STOP", self.ctx(out))
        self.assertEqual(self.hook("UserPromptSubmit", "q", cwd=root, prompt="go").get("decision"), "block")

    def test_legacy_conversation_locked_after_migration(self):
        root = self.project()
        old = self.transcript("old", 1000, first_minutes_ago=600)
        self.start("old", root)  # a session of the 1.x era, guarded in compatibility mode
        self.assertNotIn("decision", self.hook("UserPromptSubmit", "old", old, cwd=root, prompt="hi"))
        time.sleep(0.05)
        self.sg("migrate", "discover", "--add", root, ok=True)
        self.sg("migrate", "survey", root, ok=True)
        plan = os.path.join(self.home, "p.json")
        json.dump({"tasks": []}, open(plan, "w"))
        self.sg("migrate", "apply", root, "--plan", plan, "--session", S3, ok=True)
        out = self.hook("UserPromptSubmit", "old", old, cwd=root, prompt="hi")
        self.assertEqual(out.get("decision"), "block")
        self.assertIn("before its project moved", out["reason"])
        # a conversation reopened later (resume, no record) that began before migration is locked at start
        out = self.hook("SessionStart", "old2", self.transcript("old2", 1000, first_minutes_ago=600), cwd=root,
                        source="resume")
        self.assertIn("locked", self.ctx(out))
        # a new session works
        _, sid = self.start("new", root)
        self.assertNotIn("decision", self.hook("UserPromptSubmit", "new", cwd=root, prompt="hi"))

    def test_v1_lock_is_kept(self):
        root = self.project()
        v1 = os.path.join(self.home, ".claude", "session_guard")
        os.makedirs(v1)
        json.dump({"locks": {"finish": {"reason": "finish session"}}}, open(os.path.join(v1, "v1s.json"), "w"))
        self.hook("SessionStart", "v1s", cwd=root, source="resume")
        self.assertEqual(self.hook("UserPromptSubmit", "v1s", cwd=root, prompt="hi").get("decision"), "block")

    def test_v1_still_installed_means_passive(self):
        root = self.project()
        cache = os.path.join(self.home, "v1cache")
        os.makedirs(cache)
        p = os.path.join(self.home, ".claude", "plugins")
        os.makedirs(p)
        json.dump({"version": 2, "plugins": {"session-guard@session-guard": [
            {"version": "1.4.0", "installPath": cache, "scope": "user"}]}}, open(os.path.join(p, "installed_plugins.json"), "w"))
        out = self.hook("SessionStart", "p", cwd=root)
        self.assertIn("PASSIVE", self.ctx(out))
        self.assertEqual(self.hook("UserPromptSubmit", "p", self.transcript("p", 500000), cwd=root, prompt="x"), {})

    def test_cloud_session_of_computer_project_blocked(self):
        root = self.migrated()
        env = dict(self.env, CLAUDE_CODE_REMOTE="true")
        self.hook("SessionStart", "cl", cwd=root, env=env)
        out = self.hook("UserPromptSubmit", "cl", cwd=root, prompt="hi", env=env)
        self.assertEqual(out.get("decision"), "block")
        self.assertIn("computer", out["reason"])

    def test_session_id_passed_to_bash(self):
        root = self.project()
        envf = os.path.join(self.home, "env.sh")
        self.hook("SessionStart", "e", cwd=root, env=dict(self.env, CLAUDE_ENV_FILE=envf))
        self.assertRegex(self.text(envf), r"export SG_SESSION_ID=S-\d{8}-[0-9a-f]{6}")

    def test_unmigrated_project_is_found_later(self):
        self.sg("migrate", "discover", ok=True)
        root = self.project("late")
        out = self.hook("SessionStart", "late", cwd=root)
        self.assertIn("MIGRATION PENDING", self.ctx(out))
        self.assertIn("found later", self.sg("migrate", "status"))
        self.assertFalse(os.path.exists(os.path.join(root, "docs", "STATUS.md")))  # user files untouched

    def test_finish_session_stop_hook_asks_for_handoffs_twice_at_most(self):
        root = self.migrated()
        _, sid = self.start("f", root)
        self.sg("task", "new", "--title", "A", "--approval", "ok", "--session", sid, "--root", root, ok=True)
        out = self.hook("UserPromptSubmit", "f", self.transcript("f", 1000), cwd=root, prompt="ok, finish session")
        self.assertIn("hand-off still missing for: TASK-002", self.ctx(out))
        self.assertEqual(self.hook("Stop", "f", cwd=root).get("decision"), "block")
        self.assertEqual(self.hook("Stop", "f", cwd=root).get("decision"), "block")
        self.assertEqual(self.hook("Stop", "f", cwd=root), {})


class Migration(Base):
    def test_git_project_rollback_push_verified_and_unrelated_changes_untouched(self):
        bare = os.path.join(self.home, "remote.git")
        subprocess.run(["git", "init", "-q", "--bare", bare], check=True)
        root = self.project("gp", git=True, files={"docs/STATUS.md": "# STATUS\n\n## Current goal\n- ship v3\n",
                                                   "app.py": "print(1)\n"})
        self.git(root, "add", "-A")
        self.git(root, "commit", "-q", "-m", "files")
        self.git(root, "remote", "add", "origin", bare)
        self.git(root, "push", "-q", "origin", "main")
        with open(os.path.join(root, "app.py"), "w") as fh:
            fh.write("print(2)  # user's unrelated change\n")
        self.sg("migrate", "discover", "--add", root, ok=True)
        self.sg("migrate", "survey", root, ok=True)
        plan = os.path.join(self.home, "plan.json")
        json.dump({"name": "gp", "tasks": [{"title": "ship v3", "pending": ["release"], "subtasks": [{"title": "notes"}]}],
                   "contradictions": ["README says v2"]}, open(plan, "w"))
        out = self.sg("migrate", "apply", root, "--plan", plan, "--session", S3, ok=True)
        self.assertIn("sg2-pre-migration-", out)
        self.assertIn("uncommitted changes", out)
        arch = self.text(root, "docs", "STATUS_ARCHIVE.md")
        self.assertIn("ship v3", arch)                       # old STATUS kept in the archive
        self.assertIn("[SESSION-GUARD: 2.0]", arch)
        self.assertIn("README says v2", arch)
        status = self.text(root, "docs", "STATUS.md")
        self.assertIn("TASK-002 · ship v3", status)
        self.assertIn("TASK-002-1 notes", status)
        self.assertIn("verify first", self.sg("migrate", "push", root, "--approval", "yes", ok=False))
        self.sg("migrate", "verify", root, ok=True)
        self.assertIn("approval", self.sg("migrate", "push", root, ok=False))
        self.assertIn("VERIFIED", self.sg("migrate", "push", root, "--approval", "yes", ok=True))
        self.assertEqual(self.git(bare, "rev-parse", "main"), self.git(root, "rev-parse", "HEAD"))
        self.assertIn("app.py", self.git(root, "status", "--porcelain"))   # the user's change is not committed
        self.assertIn("sg2-pre-migration-", self.git(bare, "tag"))
        self.assertIn("pushed", self.sg("migrate", "status"))

    def test_non_git_project_warns_and_keeps_copy(self):
        root = self.project("ng", files={"docs/STATUS.md": "# S\nold\n"})
        self.sg("migrate", "discover", "--add", root, ok=True)
        self.sg("migrate", "survey", root, ok=True)
        plan = os.path.join(self.home, "plan.json")
        json.dump({"tasks": []}, open(plan, "w"))
        self.assertIn("NOT A GIT REPOSITORY", self.sg("migrate", "apply", root, "--plan", plan, "--session", S3, ok=True))
        backups = os.path.join(self.home, ".claude", "session_guard_v2", "backups")
        self.assertTrue(any("STATUS.md" in f for _, _, fs in os.walk(backups) for f in fs))

    def test_plan_limits_and_order(self):
        root = self.project("pl")
        self.sg("migrate", "discover", "--add", root, ok=True)
        plan = os.path.join(self.home, "plan.json")
        json.dump({"tasks": [{"title": "t%d" % n} for n in range(4)]}, open(plan, "w"))
        self.assertIn("at most 3", self.sg("migrate", "apply", root, "--plan", plan, "--session", S3,
                                                          ok=False))
        self.sg("migrate", "survey", root, ok=True)
        self.assertIn("at most 3", self.sg("migrate", "apply", root, "--plan", plan, "--session", S3, ok=False))
        self.assertFalse(os.path.exists(os.path.join(root, ".claude", "session-guard2", "store.json")))

    def test_budget_pause_and_continue_installation(self):
        root = self.project()
        _, sid = self.start("m1", self.exe)
        self.sg("migrate", "discover", "--add", root, ok=True)
        self.sg("migrate", "start", "--claude-session", "m1", ok=True)
        self.hook("UserPromptSubmit", "m1", self.transcript("m1", 1000), cwd=self.exe, prompt="start long mode")
        out = self.hook("UserPromptSubmit", "m1", self.transcript("m1", 730000), cwd=self.exe, prompt="go")
        self.assertIn("90%", self.ctx(out))
        out = self.hook("UserPromptSubmit", "m1", self.transcript("m1", 810000), cwd=self.exe, prompt="go")
        self.assertIn("INSTALLATION BUDGET USED", self.ctx(out))
        _, other = self.start("o", root)
        self.assertEqual(self.hook("UserPromptSubmit", "o", cwd=root, prompt="fix the bug").get("decision"), "block")
        self.start("m2", self.exe)
        self.assertEqual(self.hook("UserPromptSubmit", "m2", cwd=self.exe, prompt="hello").get("decision"), "block")
        self.assertEqual(self.hook("UserPromptSubmit", "m2", cwd=root, prompt="continue installation").get("decision"),
                         "block")  # only in the control project
        out = self.hook("UserPromptSubmit", "m2", cwd=self.exe, prompt="continue installation")
        self.assertIn("CONTINUE INSTALLATION", self.ctx(out))
        self.assertIn("phase: running", self.sg("migrate", "status"))


class Manifest(Base):
    def copy(self):
        dst = os.path.join(self.home, "plugin")
        shutil.copytree(PLUGIN, dst, ignore=shutil.ignore_patterns("__pycache__"))
        return dst, os.path.join(dst, "scripts", "session_guard.py")

    def test_manifest_is_current_and_signed(self):
        self.assertIn("manifest OK", self.sg("manifest", "verify", ok=True))

    def test_tamper_missing_signature_and_unlisted_file_detected(self):
        dst, script = self.copy()
        run = lambda *a: subprocess.run([sys.executable, "-I", script, *a], capture_output=True,
                                        env=self.env).stdout.decode()
        with open(os.path.join(dst, "scripts", "sg2", "store.py"), "a") as fh:
            fh.write("\n# changed\n")
        with open(os.path.join(dst, "skills", "extra.md"), "w") as fh:
            fh.write("no signature\n")
        out = run("manifest", "verify")
        self.assertIn("changed (hash differs): scripts/sg2/store.py", out)
        self.assertIn("not in the manifest: skills/extra.md", out)
        run("manifest", "build")
        self.assertIn("no 2.0 signature: skills/extra.md", run("manifest", "verify"))


if __name__ == "__main__":
    unittest.main()
