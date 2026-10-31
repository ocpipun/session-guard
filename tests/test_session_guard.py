"""Tests for Session Guard. Run: python -m unittest discover -s tests

Each test runs the hook script as Claude Code would (JSON on stdin) against a
fake transcript, with HOME pointed at a temporary folder.
"""
import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "plugins", "session-guard", "scripts", "session_guard.py")
SETUP = os.path.join(ROOT, "plugins", "session-guard", "scripts", "cloud_setup.py")


def stamp(minutes_ago):
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat().replace("+00:00", "Z")


class Guard(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.env = dict(os.environ, HOME=self.home, USERPROFILE=self.home)
        self.env.pop("CLAUDE_PROJECT_DIR", None)  # tests run inside Claude Code: never touch the real project
        self.cli("--setup", "approved")

    def cli(self, *args, script=SCRIPT):
        r = subprocess.run([sys.executable, "-I", script, *args], capture_output=True, env=self.env)
        return r.stdout.decode("utf-8")

    def project(self, name="proj", files=None):
        root = os.path.join(self.home, "work", name)
        os.makedirs(root, exist_ok=True)
        for rel, text in (files or {}).items():
            os.makedirs(os.path.dirname(os.path.join(root, rel)) or root, exist_ok=True)
            with open(os.path.join(root, rel), "w", encoding="utf-8") as fh:
                fh.write(text)
        return root

    def read(self, *parts):
        with open(os.path.join(*parts), encoding="utf-8") as fh:
            return fh.read()

    def tearDown(self):
        shutil.rmtree(self.home, ignore_errors=True)

    def transcript(self, name, context, minutes_ago=5, msg_ids=("msg_a",), extra=()):
        path = os.path.join(self.home, name + ".jsonl")
        with open(path, "w", encoding="utf-8") as fh:
            for mid in msg_ids:
                fh.write(json.dumps({"type": "assistant", "timestamp": stamp(minutes_ago), "message": {
                    "id": mid, "usage": {"input_tokens": 2, "cache_creation_input_tokens": 1000,
                                         "cache_read_input_tokens": context - 1002}}}) + "\n")
            for entry in extra:
                fh.write(json.dumps(entry) + "\n")
        return path

    def run_hook(self, event, sid, transcript="none.jsonl", prompt="", args=(), cwd=None):
        data = {"hook_event_name": event, "session_id": sid, "transcript_path": transcript,
                "prompt": prompt, "cwd": cwd or self.home, "source": "startup"}
        out = subprocess.run([sys.executable, "-I", SCRIPT, *args], input=json.dumps(data).encode(),
                             capture_output=True, env=self.env).stdout.decode()
        return json.loads(out) if out else {}

    def context(self, result):
        return (result.get("hookSpecificOutput") or {}).get("additionalContext", "")

    # --- thresholds -------------------------------------------------------
    def test_below_250k_only_status_line(self):
        r = self.run_hook("UserPromptSubmit", "s1", self.transcript("t", 100_000), "hello")
        self.assertIn("Context: 100k", self.context(r))
        self.assertNotIn("systemMessage", r)

    def test_250k_notice_once(self):
        t = self.transcript("t", 260_000)
        first = self.run_hook("UserPromptSubmit", "s1", t, "go")
        second = self.run_hook("UserPromptSubmit", "s1", t, "go")
        self.assertIn("250k", first["systemMessage"])
        self.assertNotIn("NOTICE", self.context(second))

    def test_300k_hand_off_repeats(self):
        t = self.transcript("t", 310_000)
        self.run_hook("UserPromptSubmit", "s1", t, "go")
        self.assertIn("HAND-OFF", self.context(self.run_hook("UserPromptSubmit", "s1", t, "go")))

    def test_400k_blocks_and_stays_locked(self):
        self.assertEqual(self.run_hook("UserPromptSubmit", "s1", self.transcript("t", 410_000), "go")["decision"], "block")
        self.assertEqual(self.run_hook("UserPromptSubmit", "s1", self.transcript("t", 1_000), "hi")["decision"], "block")

    def test_hard_limit_during_work_stops_turn(self):
        r = self.run_hook("PostToolUse", "s1", self.transcript("t", 402_000))
        self.assertIs(r["continue"], False)

    def test_threshold_during_work_told_once(self):
        t = self.transcript("t", 305_000)
        self.assertIn("HAND-OFF", self.context(self.run_hook("PostToolUse", "s1", t)))
        self.assertEqual(self.run_hook("PostToolUse", "s1", t), {})

    # --- long mode --------------------------------------------------------
    def test_long_mode_thresholds(self):
        self.assertIn("Mode: LONG", self.context(self.run_hook("UserPromptSubmit", "s1", self.transcript("t", 450_000), "start long mode")))
        self.assertIn("700k", self.context(self.run_hook("UserPromptSubmit", "s1", self.transcript("t", 750_000), "go")))
        self.assertIn("NO WORK", self.context(self.run_hook("UserPromptSubmit", "s1", self.transcript("t", 850_000), "q")))
        self.assertEqual(self.run_hook("UserPromptSubmit", "s1", self.transcript("t", 905_000), "q")["decision"], "block")

    def test_command_positions(self):
        cases = [("Start long mode, render the clips", True), ("ok then, START LONG MODE", True),
                 ("about your 'start long mode' warning", False), ("start long mode does what?", False)]
        for i, (prompt, expected) in enumerate(cases):
            # separate conversations: separate message ids (shared ids would mean a reopened conversation)
            r = self.run_hook("UserPromptSubmit", "c%d" % i, self.transcript("t%d" % i, 100_000, msg_ids=("m%d" % i,)), prompt)
            self.assertEqual("Mode: LONG" in self.context(r), expected, prompt)

    def test_turkish_keyboard_uppercase(self):
        r = self.run_hook("UserPromptSubmit", "s1", self.transcript("t", 100_000), "işim bitti, FİNİSH SESSİON")
        self.assertIn("FINISH SESSION", self.context(r))

    # --- break rule -------------------------------------------------------
    def test_break_small_context_reminds(self):
        r = self.run_hook("UserPromptSubmit", "s1", self.transcript("t", 100_000, minutes_ago=90), "do it")
        self.assertIn("BREAK REMINDER", self.context(r))

    def test_break_large_context_hands_off_then_locks(self):
        t = self.transcript("t", 200_000, minutes_ago=90)
        self.assertIn("BREAK RULE", self.context(self.run_hook("UserPromptSubmit", "s1", t, "do it")))
        self.assertEqual(self.run_hook("UserPromptSubmit", "s1", t, "hi")["decision"], "block")

    def test_break_ignored_in_long_mode(self):
        self.run_hook("UserPromptSubmit", "s1", self.transcript("t", 100_000), "start long mode")
        r = self.run_hook("UserPromptSubmit", "s1", self.transcript("t", 200_000, minutes_ago=90), "go")
        self.assertNotIn("BREAK", self.context(r))

    def test_break_ignored_while_background_job_runs(self):
        launch = {"type": "assistant", "timestamp": stamp(90), "message": {"id": "msg_b", "content": [
            {"type": "tool_use", "id": "toolu_1", "name": "Bash", "input": {"command": "x", "run_in_background": True}}]}}
        r = self.run_hook("UserPromptSubmit", "s1", self.transcript("t", 200_000, 90, extra=[launch]), "status?")
        self.assertIn("Background jobs still running: 1", self.context(r))
        self.assertNotIn("BREAK", self.context(r))

    def test_break_applies_after_background_job_finished(self):
        launch = {"type": "assistant", "timestamp": stamp(90), "message": {"id": "msg_b", "content": [
            {"type": "tool_use", "id": "toolu_1", "name": "Bash", "input": {"command": "x", "run_in_background": True}}]}}
        done = {"type": "user", "timestamp": stamp(89), "message": {"content":
                "<task-notification>\n<tool-use-id>toolu_1</tool-use-id>\n<status>completed</status>"}}
        r = self.run_hook("UserPromptSubmit", "s1", self.transcript("t", 200_000, 90, extra=[launch, done]), "status?")
        self.assertIn("BREAK RULE", self.context(r))

    # --- 24-hour watch (async hook, shortened to seconds) -------------------
    def watch_proc(self, transcript, seconds):
        env = dict(self.env, SESSION_GUARD_WATCH_SECONDS=str(seconds))
        data = json.dumps({"hook_event_name": "UserPromptSubmit", "session_id": "w1", "transcript_path": transcript,
                           "cwd": self.home, "prompt": "go"}).encode()
        proc = subprocess.Popen([sys.executable, "-I", SCRIPT, "--watch"], stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
        proc.stdin.write(data)
        proc.stdin.close()
        return proc

    def busy_transcript(self):
        tool = {"type": "assistant", "timestamp": stamp(1), "message": {"id": "m7", "content": [
            {"type": "tool_use", "id": "toolu_9", "name": "Bash", "input": {"command": "render"}}]}}
        return self.transcript("busy", 100_000, extra=[tool])

    def test_watch_wakes_claude_when_still_busy(self):
        proc = self.watch_proc(self.busy_transcript(), 1)
        _, err = proc.communicate(timeout=30)
        self.assertEqual(proc.returncode, 2)
        self.assertIn(b"24-HOUR WATCH", err)

    def test_watch_quiet_when_turn_finished(self):
        done = {"type": "assistant", "timestamp": stamp(1), "message": {"id": "m8", "content": [{"type": "text", "text": "done"}]}}
        proc = self.watch_proc(self.transcript("idle", 100_000, extra=[done]), 1)
        proc.communicate(timeout=30)
        self.assertEqual(proc.returncode, 0)

    def test_newer_prompt_replaces_older_watch(self):
        older = self.watch_proc(self.busy_transcript(), 3)
        time.sleep(1)
        newer = self.watch_proc(self.busy_transcript(), 6)
        older.communicate(timeout=30)
        self.assertEqual(older.returncode, 0)
        newer.kill()
        newer.communicate()

    def test_finished_subagent_is_not_pending(self):
        launch = {"type": "assistant", "timestamp": stamp(90), "message": {"id": "msg_b", "content": [
            {"type": "tool_use", "id": "toolu_2", "name": "Agent", "input": {"prompt": "x"}}]}}
        result = {"type": "user", "timestamp": stamp(90), "message": {"content": [
            {"type": "tool_result", "tool_use_id": "toolu_2", "content": "Async agent launched. agentId: a1b2c3"}]}}
        back = {"type": "user", "timestamp": stamp(85), "message": {"content": '<agent-message from="a1b2c3">report</agent-message>'}}
        r = self.run_hook("UserPromptSubmit", "s1", self.transcript("t", 200_000, 90, extra=[launch, result, back]), "next")
        self.assertIn("BREAK RULE", self.context(r))

    # --- 24 hours ---------------------------------------------------------
    def test_job_open_over_24h_warns_user(self):
        launch = {"type": "assistant", "timestamp": stamp(25 * 60), "message": {"id": "msg_b", "content": [
            {"type": "tool_use", "id": "toolu_1", "name": "Bash", "input": {"command": "x", "run_in_background": True}}]}}
        t = self.transcript("t", 100_000, 5, extra=[launch])
        r = self.run_hook("UserPromptSubmit", "s1", t, "status?")
        self.assertIn("finish session", r["systemMessage"])
        self.assertIn("WARNING (24 hours): 1", self.context(r))
        self.assertNotIn("systemMessage", self.run_hook("UserPromptSubmit", "s1", t, "again"))

    def test_turn_over_24h_warns_once(self):
        prompt = {"type": "user", "timestamp": stamp(25 * 60), "message": {"role": "user", "content": "render all"}}
        reminder = {"type": "user", "timestamp": stamp(60), "message": {"content": "<task-notification>x"}}
        path = os.path.join(self.home, "turn.jsonl")
        with open(path, "w", encoding="utf-8") as fh:
            for entry in (prompt, reminder):
                fh.write(json.dumps(entry) + "\n")
            fh.write(json.dumps({"type": "assistant", "timestamp": stamp(1), "message": {"id": "m9", "usage": {
                "input_tokens": 1, "cache_read_input_tokens": 50_000}}}) + "\n")
        self.assertIn("same user message for over 24 hours", self.context(self.run_hook("PostToolUse", "s1", path)))
        self.assertEqual(self.run_hook("PostToolUse", "s1", path), {})

    def test_old_records_deleted_but_locked_kept(self):
        folder = os.path.join(self.home, ".claude", "session_guard")
        old = time.time() - 40 * 86400
        for name, locked in (("free", False), ("closed", True)):
            path = os.path.join(folder, name + ".json")
            with open(path, "w") as fh:
                json.dump({"locked": locked}, fh)
            os.utime(path, (old, old))
        self.run_hook("SessionStart", "s1")
        self.assertEqual(sorted(os.listdir(folder)), ["closed.json", "config"])

    # --- finish session and reopened conversations ------------------------
    def test_finish_session_locks(self):
        t = self.transcript("t", 120_000)
        self.assertIn("FINISH SESSION", self.context(self.run_hook("UserPromptSubmit", "s1", t, "done for today, finish session")))
        self.assertEqual(self.run_hook("UserPromptSubmit", "s1", t, "one more thing")["decision"], "block")

    def test_finish_session_in_sentence_is_ignored(self):
        r = self.run_hook("UserPromptSubmit", "s1", self.transcript("t", 120_000), "what happens on finish session? tell me")
        self.assertNotIn("FINISH SESSION", self.context(r))

    def test_reopened_conversation_keeps_lock(self):
        self.run_hook("UserPromptSubmit", "old", self.transcript("old", 120_000, msg_ids=("m1", "m2")), "finish session")
        reopened = self.transcript("new", 120_000, msg_ids=("m1", "m3"))  # history copied, new session id
        self.assertEqual(self.run_hook("UserPromptSubmit", "new", reopened, "hi again")["decision"], "block")

    def test_reopened_conversation_keeps_long_mode(self):
        self.run_hook("UserPromptSubmit", "old", self.transcript("old", 120_000, msg_ids=("m1",)), "start long mode")
        r = self.run_hook("UserPromptSubmit", "new", self.transcript("new", 450_000, msg_ids=("m1", "m4")), "go")
        self.assertIn("Mode: LONG", self.context(r))

    # --- session start, report, cloud copy --------------------------------
    def test_session_start_gives_rules_and_status(self):
        root = self.project(files={"docs/STATUS.md": "# STATUS\nopen item"})
        ctx = self.context(self.run_hook("SessionStart", "s1", cwd=root))
        self.assertIn("Reply style", ctx)
        self.assertIn("open item", ctx)

    def test_bad_input_never_fails(self):
        r = subprocess.run([sys.executable, "-I", SCRIPT], input=b"not json", capture_output=True, env=self.env)
        self.assertEqual(r.returncode, 0)

    def test_facts_rule_given(self):
        self.assertIn("not researched", self.context(self.run_hook("SessionStart", "s1")))

    # --- setup gate -------------------------------------------------------
    def test_inactive_until_setup(self):
        os.remove(os.path.join(self.home, ".claude", "session_guard", "config", "setup.json"))
        start = self.run_hook("SessionStart", "s1")
        self.assertIn("INACTIVE", self.context(start))
        self.assertNotIn("Reply style", self.context(start))
        self.assertEqual(self.run_hook("UserPromptSubmit", "s1", self.transcript("t", 410_000), "go"), {})

    def test_old_rules_version_needs_setup_again(self):
        path = os.path.join(self.home, ".claude", "session_guard", "config", "setup.json")
        with open(path, "w") as fh:
            json.dump({"rules": 1, "global": "approved", "projects": {}}, fh)
        self.assertIn("INACTIVE", self.context(self.run_hook("SessionStart", "s1")))

    def test_declined_is_off(self):
        self.cli("--setup", "declined")
        self.assertIn("OFF", self.context(self.run_hook("SessionStart", "s1")))
        self.assertEqual(self.run_hook("UserPromptSubmit", "s1", self.transcript("t", 410_000), "go"), {})

    def test_project_with_rules_needs_review_then_works(self):
        root = self.project(files={"CLAUDE.md": "# Old\n- Hand off at 250k tokens.\n"})
        self.assertIn("INACTIVE in this project", self.context(self.run_hook("SessionStart", "s1", cwd=root)))
        self.assertFalse(os.path.exists(os.path.join(root, "docs")))
        self.assertIn("Hand off at 250k", self.cli("--scan", root))
        self.cli("--setup", "approved", root)
        ctx = self.context(self.run_hook("SessionStart", "s1", cwd=root))
        self.assertIn("Reply style", ctx)
        self.assertEqual(self.read(root, "CLAUDE.md"), "# Old\n- Hand off at 250k tokens.\n")
        self.assertTrue(os.path.exists(os.path.join(root, "docs", "STATUS.md")))

    def test_project_declined_is_off_there_only(self):
        root = self.project(files={"CLAUDE.md": "rules"})
        self.cli("--setup", "declined", root)
        self.assertIn("OFF in this project", self.context(self.run_hook("SessionStart", "s1", cwd=root)))
        self.assertIn("Reply style", self.context(self.run_hook("SessionStart", "s2", cwd=self.project("other"))))

    # --- project files ----------------------------------------------------
    def test_new_project_files_created_once(self):
        root = self.project()
        ctx = self.context(self.run_hook("SessionStart", "s1", cwd=root))
        self.assertIn("CLAUDE.md, docs/STATUS.md, docs/STATUS_ARCHIVE.md", ctx)
        with open(os.path.join(root, "docs", "STATUS.md"), "w", encoding="utf-8") as fh:
            fh.write("# STATUS\nmy work")
        os.remove(os.path.join(root, "CLAUDE.md"))
        with open(os.path.join(root, "CLAUDE.md"), "w", encoding="utf-8") as fh:
            fh.write("mine")
        ctx = self.context(self.run_hook("SessionStart", "s2", cwd=root))
        self.assertNotIn("created", ctx)
        self.assertIn("my work", ctx)
        self.assertEqual(self.read(root, "CLAUDE.md"), "mine")

    def test_existing_files_in_other_case_or_place_are_kept(self):
        root = self.project(files={".claude/CLAUDE.md": "rules", "docs/status.md": "lower case status"})
        self.cli("--setup", "approved", root)
        ctx = self.context(self.run_hook("SessionStart", "s1", cwd=root))
        self.assertIn("lower case status", ctx)
        self.assertEqual(sorted(os.listdir(root)), [".claude", "docs"])
        self.assertEqual(sorted(n.lower() for n in os.listdir(os.path.join(root, "docs"))), ["status.md", "status_archive.md"])

    def test_home_is_not_a_project(self):
        self.run_hook("SessionStart", "s1", cwd=self.home)
        self.assertFalse(os.path.exists(os.path.join(self.home, "docs")))
        self.assertFalse(os.path.exists(os.path.join(self.home, "CLAUDE.md")))

    def test_project_dir_wins_over_subfolder_cwd(self):
        root = self.project(files={"docs/STATUS.md": "root status"})
        self.env["CLAUDE_PROJECT_DIR"] = root
        ctx = self.context(self.run_hook("SessionStart", "s1", cwd=os.path.join(root, "docs")))
        self.assertIn("root status", ctx)
        self.assertFalse(os.path.exists(os.path.join(root, "docs", "docs")))

    def test_worktree_shares_review_of_main_checkout(self):
        main = self.project("repo", {"CLAUDE.md": "rules"})
        os.makedirs(os.path.join(main, ".git", "worktrees", "wt"))
        tree = os.path.join(main, ".claude", "worktrees", "wt")
        os.makedirs(tree)
        with open(os.path.join(tree, ".git"), "w") as fh:
            fh.write("gitdir: %s\n" % os.path.join(main, ".git", "worktrees", "wt"))
        with open(os.path.join(tree, "CLAUDE.md"), "w") as fh:
            fh.write("rules")
        self.cli("--setup", "approved", main)
        self.env["CLAUDE_PROJECT_DIR"] = tree
        self.assertIn("Reply style", self.context(self.run_hook("SessionStart", "s1", cwd=tree)))
        self.assertFalse(os.path.exists(os.path.join(tree, "docs")))

    # --- global CLAUDE.md, scan, backup -----------------------------------
    def test_global_block_added_kept_and_refreshed(self):
        path = os.path.join(self.home, ".claude", "CLAUDE.md")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("# My rules\n- Use tabs.\n")
        self.cli("--install-global")
        self.cli("--install-global")
        text = self.read(path)
        self.assertEqual(text.count("session-guard:begin"), 1)
        self.assertIn("- Use tabs.", text)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text.replace("Topics", "Topix"))
        self.assertIn("refreshed", self.context(self.run_hook("SessionStart", "s1")))
        self.assertIn("## Topics", self.read(path))

    def test_global_block_created_when_missing(self):
        self.cli("--install-global")
        self.assertIn("Session Guard general rules", self.read(self.home, ".claude", "CLAUDE.md"))

    def test_damaged_markers_left_alone(self):
        path = os.path.join(self.home, ".claude", "CLAUDE.md")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("<!-- session-guard:begin -->\nhalf")
        self.assertIn("broken", self.cli("--install-global"))
        self.assertEqual(self.read(path), "<!-- session-guard:begin -->\nhalf")

    def test_scan_finds_projects_skills_and_hooks(self):
        root = self.project(files={"CLAUDE.md": "- Devir notu 300k bağlamda yazılır.\n- Use tabs.\n"})
        folder = os.path.join(self.home, ".claude", "projects", "x")
        os.makedirs(folder)
        with open(os.path.join(folder, "a.jsonl"), "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"type": "user", "cwd": root}) + "\n")
        os.makedirs(os.path.join(self.home, ".claude", "skills", "old-guard"))
        with open(os.path.join(self.home, ".claude", "skills", "old-guard", "SKILL.md"), "w") as fh:
            fh.write("Hand-off when context passes 250k")
        with open(os.path.join(self.home, ".claude", "settings.json"), "w") as fh:
            json.dump({"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "python ctx.py"}]}]}}, fh)
        report = self.cli("--scan")
        for expected in ("PROJECT " + root, "Devir notu 300k", "old-guard", "context passes 250k", "Stop: python ctx.py"):
            self.assertIn(expected, report)
        self.assertNotIn("Use tabs", report)
        self.cli("--setup", "approved", "--all")
        self.assertIn("Reply style", self.context(self.run_hook("SessionStart", "s1", cwd=root)))

    # --- cloud copy -------------------------------------------------------
    def test_cloud_setup_refuses_unreviewed_repo(self):
        repo = self.project("repo", {"CLAUDE.md": "old rules"})
        r = subprocess.run([sys.executable, "-I", SETUP, repo], capture_output=True, env=self.env)
        self.assertEqual(r.returncode, 1)
        self.assertFalse(os.path.exists(os.path.join(repo, ".claude")))

    def test_cloud_install_after_review_in_cloud(self):
        repo = self.project("repo", {"CLAUDE.md": "reviewed rules"})
        shutil.rmtree(os.path.join(self.home, ".claude"))  # a cloud machine: no plugin, no setup record
        subprocess.run([sys.executable, "-I", SETUP, "--reviewed", repo], capture_output=True, env=self.env, check=True)
        self.assertEqual(self.read(repo, "CLAUDE.md"), "reviewed rules")
        cloud = dict(self.env, CLAUDE_PROJECT_DIR=repo)
        data = json.dumps({"hook_event_name": "SessionStart", "session_id": "s", "source": "startup"}).encode()
        copy = os.path.join(repo, ".claude", "session-guard", "session_guard.py")
        r = subprocess.run([sys.executable, "-I", copy, "--project"], input=data, capture_output=True, env=cloud)
        self.assertIn("Reply style", json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"])

    def test_cloud_setup_is_idempotent_and_quiet_with_plugin(self):
        repo = self.project("repo", {".claude/settings.json": json.dumps({"permissions": {"allow": ["Bash(ls)"]}})})
        for _ in range(2):
            subprocess.run([sys.executable, "-I", SETUP, repo], capture_output=True, env=self.env, check=True)
        with open(os.path.join(repo, ".claude", "settings.json")) as fh:
            settings = json.load(fh)
        self.assertEqual(settings["permissions"]["allow"], ["Bash(ls)"])
        self.assertEqual(len(settings["hooks"]["UserPromptSubmit"]), 2)  # guard + 24-hour watch
        self.assertIn("name: session-guard-setup", self.read(repo, ".claude", "skills", "session-guard-setup", "SKILL.md"))
        self.assertTrue(os.path.exists(os.path.join(repo, "docs", "STATUS_ARCHIVE.md")))
        copy = os.path.join(repo, ".claude", "session-guard", "session_guard.py")
        cloud = dict(self.env, CLAUDE_PROJECT_DIR=repo)
        shutil.rmtree(os.path.join(self.home, ".claude", "session_guard"))  # the cloud has no setup.json of the user
        data = json.dumps({"hook_event_name": "SessionStart", "session_id": "s", "source": "startup"}).encode()
        alone = subprocess.run([sys.executable, "-I", copy, "--project"], input=data, capture_output=True, env=cloud)
        ctx = json.loads(alone.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("Session Guard general rules", ctx)  # no global CLAUDE.md in the cloud
        os.makedirs(os.path.join(self.home, ".claude", "plugins", "cache", "sg", "scripts"))
        shutil.copyfile(SCRIPT, os.path.join(self.home, ".claude", "plugins", "cache", "sg", "scripts", "session_guard.py"))
        quiet = subprocess.run([sys.executable, "-I", copy, "--project"], input=data, capture_output=True, env=cloud)
        self.assertEqual(quiet.stdout, b"")

    def test_cloud_copy_creates_files_or_asks_for_review(self):
        bare = self.project("bare")
        cloud = dict(self.env, CLAUDE_PROJECT_DIR=bare)
        data = json.dumps({"hook_event_name": "SessionStart", "session_id": "s", "source": "startup"}).encode()
        r = subprocess.run([sys.executable, "-I", SCRIPT, "--project"], input=data, capture_output=True, env=cloud)
        self.assertIn("Commit them", json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"])
        self.assertTrue(os.path.exists(os.path.join(bare, "CLAUDE.md")))
        changed = self.project("changed", {"CLAUDE.md": "new unreviewed rules"})
        cloud["CLAUDE_PROJECT_DIR"] = changed
        r = subprocess.run([sys.executable, "-I", SCRIPT, "--project"], input=data, capture_output=True, env=cloud)
        self.assertIn("session-guard-setup", json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"])
        self.cli("--project", "--setup", "approved", changed)
        r = subprocess.run([sys.executable, "-I", SCRIPT, "--project"], input=data, capture_output=True, env=cloud)
        self.assertIn("Reply style", json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"])


if __name__ == "__main__":
    unittest.main()
