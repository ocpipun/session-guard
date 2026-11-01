"""Tests for Session Guard. Run: python -m unittest discover -s tests

Each test runs the hook script as Claude Code would (JSON on stdin) against a
fake transcript, with HOME pointed at a temporary folder.
"""
import glob
import json
import os
import re
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
        self.env.pop("CLAUDE_CODE_ENTRYPOINT", None)  # quota readings are asked for in the desktop app only
        # the desktop app's language setting is looked up here: never the real one
        self.env.update(LOCALAPPDATA=os.path.join(self.home, "local"), APPDATA=os.path.join(self.home, "roaming"))
        self.cli("--setup", "approved")
        self.executive = os.path.join(self.home, "work", "session-guard-executive")
        os.makedirs(self.executive)
        self.cli("--set-executive", self.executive)

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

    def started(self, sid, rules=True):
        """The start marker SessionStart writes; without it a prompt meets the restart lock."""
        folder = os.path.join(self.home, ".claude", "session_guard", "started")
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, sid), "w") as fh:
            json.dump({"self": SCRIPT, "rules": rules}, fh)

    def run_hook(self, event, sid, transcript="none.jsonl", prompt="", args=(), cwd=None, fresh=False, source="startup", **extra):
        if event != "SessionStart" and not fresh and not os.path.exists(os.path.join(self.home, ".claude", "session_guard", "started", sid)):
            self.started(sid)
        data = dict({"hook_event_name": event, "session_id": sid, "transcript_path": transcript,
                     "prompt": prompt, "cwd": cwd or self.home, "source": source}, **extra)
        out = subprocess.run([sys.executable, "-I", SCRIPT, *args], input=json.dumps(data).encode(),
                             capture_output=True, env=self.env).stdout.decode()
        return json.loads(out) if out else {}

    def context(self, result):
        return (result.get("hookSpecificOutput") or {}).get("additionalContext", "")

    # --- thresholds -------------------------------------------------------
    def test_below_250k_adds_nothing(self):
        self.assertEqual(self.run_hook("UserPromptSubmit", "s1", self.transcript("t", 100_000), "hello"), {})

    def test_status_line_comes_with_instruction(self):
        r = self.run_hook("UserPromptSubmit", "s1", self.transcript("t", 260_000), "go")
        self.assertIn("Context: 260k", self.context(r))

    def test_250k_notice_once(self):
        t = self.transcript("t", 260_000)
        first = self.run_hook("UserPromptSubmit", "s1", t, "go")
        second = self.run_hook("UserPromptSubmit", "s1", t, "go")
        self.assertIn("250k", first["systemMessage"])
        self.assertIn("250k threshold passed", self.context(first))
        self.assertIn("This is a Session Guard notice", self.context(first))
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
        during = self.context(self.run_hook("PostToolUse", "s1", t))
        self.assertIn("HAND-OFF", during)
        self.assertIn("300k threshold passed", during)
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
        self.assertEqual(r, {})

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
        self.started("w1")
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
        self.assertEqual(sorted(os.listdir(folder)), ["closed.json", "config", "started"])

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
        self.assertIn("SETUP PENDING", self.context(start))
        self.assertNotIn("Reply style", self.context(start))
        r = self.run_hook("UserPromptSubmit", "s1", self.transcript("t", 410_000), "go")
        self.assertNotIn("decision", r)  # nothing enforced, but the setup is asked for again
        self.assertIn("SETUP PENDING", self.context(r))

    def test_old_rules_version_needs_setup_again(self):
        path = os.path.join(self.home, ".claude", "session_guard", "config", "setup.json")
        with open(path, "w") as fh:
            json.dump({"rules": 1, "global": "approved", "projects": {}}, fh)
        self.assertIn("SETUP PENDING", self.context(self.run_hook("SessionStart", "s1")))

    def test_declined_is_off(self):
        self.cli("--setup", "declined")
        self.assertIn("OFF", self.context(self.run_hook("SessionStart", "s1")))
        self.assertEqual(self.run_hook("UserPromptSubmit", "s1", self.transcript("t", 410_000), "go"), {})

    def test_project_with_rules_needs_review_then_works(self):
        root = self.project(files={"CLAUDE.md": "# Old\n- Hand off at 250k tokens.\n"})
        self.assertIn("PROJECT REVIEW PENDING", self.context(self.run_hook("SessionStart", "s1", cwd=root)))
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
    def test_cloud_install_then_review_in_next_session(self):
        repo = self.project("repo", {"CLAUDE.md": "old rules"})
        shutil.rmtree(os.path.join(self.home, ".claude"))  # a cloud machine: no plugin, no setup record
        subprocess.run([sys.executable, "-I", SETUP, repo], capture_output=True, env=self.env, check=True)
        self.assertEqual(self.read(repo, "CLAUDE.md"), "old rules")
        self.assertFalse(os.path.exists(os.path.join(repo, "docs")))
        cloud = dict(self.env, CLAUDE_CODE_REMOTE="true", CLAUDE_PROJECT_DIR=repo)
        data = json.dumps({"hook_event_name": "SessionStart", "session_id": "s", "source": "startup"}).encode()
        copy = os.path.join(repo, ".claude", "session-guard", "session_guard.py")
        r = subprocess.run([sys.executable, "-I", copy, "--project"], input=data, capture_output=True, env=cloud)
        self.assertIn("PROJECT REVIEW PENDING", json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"])
        subprocess.run([sys.executable, "-I", copy, "--project", "--setup", "approved", repo], capture_output=True, env=cloud)
        r = subprocess.run([sys.executable, "-I", copy, "--project"], input=data, capture_output=True, env=cloud)
        self.assertIn("Reply style", json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"])
        self.assertTrue(os.path.exists(os.path.join(repo, "docs", "STATUS.md")))

    def test_cloud_installing_session_goes_on(self):
        repo = self.project("repo", {"CLAUDE.md": "reviewed rules"})
        subprocess.run([sys.executable, "-I", SETUP, "--reviewed", repo], capture_output=True, env=self.env, check=True)
        self.assertTrue(os.path.exists(os.path.join(repo, "docs", "STATUS.md")))
        shutil.rmtree(os.path.join(self.home, ".claude"))
        cloud = dict(self.env, CLAUDE_CODE_REMOTE="true", CLAUDE_PROJECT_DIR=repo)
        data = json.dumps({"hook_event_name": "UserPromptSubmit", "session_id": "old", "prompt": "next"}).encode()
        copy = os.path.join(repo, ".claude", "session-guard", "session_guard.py")
        r = json.loads(subprocess.run([sys.executable, "-I", copy, "--project"], input=data, capture_output=True, env=cloud).stdout)
        self.assertNotIn("decision", r)  # the installing session goes on, with the rules
        self.assertIn("Reply style", r["hookSpecificOutput"]["additionalContext"])

    def test_cloud_setup_is_idempotent_and_quiet_with_plugin(self):
        repo = self.project("repo", {".claude/settings.json": json.dumps({"permissions": {"allow": ["Bash(ls)"]}})})
        for _ in range(2):
            subprocess.run([sys.executable, "-I", SETUP, repo], capture_output=True, env=self.env, check=True)
        with open(os.path.join(repo, ".claude", "settings.json")) as fh:
            settings = json.load(fh)
        self.assertEqual(settings["permissions"]["allow"], ["Bash(ls)"])
        self.assertEqual(len(settings["hooks"]["UserPromptSubmit"]), 2)  # guard + 24-hour watch
        self.assertIn("name: session-guard-setup", self.read(repo, ".claude", "skills", "session-guard-setup", "SKILL.md"))
        copy = os.path.join(repo, ".claude", "session-guard", "session_guard.py")
        cloud = dict(self.env, CLAUDE_CODE_REMOTE="true", CLAUDE_PROJECT_DIR=repo)
        shutil.rmtree(os.path.join(self.home, ".claude", "session_guard"))  # the cloud has no setup.json of the user
        data = json.dumps({"hook_event_name": "SessionStart", "session_id": "s", "source": "startup"}).encode()
        alone = subprocess.run([sys.executable, "-I", copy, "--project"], input=data, capture_output=True, env=cloud)
        ctx = json.loads(alone.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("Session Guard general rules", ctx)  # no global CLAUDE.md in the cloud
        self.assertTrue(os.path.exists(os.path.join(repo, "docs", "STATUS_ARCHIVE.md")))  # nothing to review
        os.makedirs(os.path.join(self.home, ".claude", "plugins", "cache", "sg", "scripts"))
        shutil.copyfile(SCRIPT, os.path.join(self.home, ".claude", "plugins", "cache", "sg", "scripts", "session_guard.py"))
        quiet = subprocess.run([sys.executable, "-I", copy, "--project"], input=data, capture_output=True, env=cloud)
        self.assertEqual(quiet.stdout, b"")

    def test_cloud_copy_creates_files_or_asks_for_review(self):
        bare = self.project("bare")
        cloud = dict(self.env, CLAUDE_CODE_REMOTE="true", CLAUDE_PROJECT_DIR=bare)
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

    # --- v1.2: break 100k, restart lock, late rules, lock types --------------
    def test_break_at_110k_hands_off(self):
        r = self.run_hook("UserPromptSubmit", "s1", self.transcript("t", 110_000, minutes_ago=90), "do it")
        self.assertIn("BREAK RULE", self.context(r))

    def test_session_started_before_install_is_restart_locked(self):
        r = self.run_hook("UserPromptSubmit", "s1", self.transcript("t", 50_000), "hi", fresh=True)
        self.assertEqual(r["decision"], "block")
        self.assertIn("Fully quit Claude", r["reason"])
        self.run_hook("SessionStart", "s1", source="resume")  # the app was restarted: the session is resumed
        r = self.run_hook("UserPromptSubmit", "s1", self.transcript("t", 50_000), "hi")
        self.assertNotIn("decision", r)
        self.assertIn("Reply style", self.context(r))  # it never got the rules: given once now
        self.assertEqual(self.run_hook("UserPromptSubmit", "s1", self.transcript("t", 50_000), "again"), {})

    def test_installing_session_runs_setup_then_locks(self):
        os.remove(os.path.join(self.home, ".claude", "session_guard", "config", "setup.json"))
        t = self.transcript("t", 50_000)
        r = self.run_hook("UserPromptSubmit", "s1", t, "yes, approve", fresh=True)
        self.assertNotIn("decision", r)  # the user's answers to the setup get through
        self.assertIn("SETUP PENDING", self.context(r))
        self.cli("--setup", "approved")
        self.cli("--set-executive", self.executive)
        self.assertIn("Fully quit Claude", self.run_hook("UserPromptSubmit", "s1", t, "next", fresh=True)["reason"])

    def test_resumed_session_with_rules_gets_nothing_again(self):
        self.run_hook("SessionStart", "s1")
        self.run_hook("SessionStart", "s1", source="resume")
        self.assertEqual(self.run_hook("UserPromptSubmit", "s1", self.transcript("t", 50_000), "hi"), {})

    def test_setup_takes_effect_at_once_in_open_sessions(self):
        os.remove(os.path.join(self.home, ".claude", "session_guard", "config", "setup.json"))
        self.run_hook("SessionStart", "s1")
        self.run_hook("SessionStart", "s2")
        self.cli("--setup", "approved")
        self.cli("--set-executive", self.executive)
        for sid in ("s1", "s2"):
            r = self.run_hook("UserPromptSubmit", sid, self.transcript(sid, 50_000, msg_ids=(sid,)), "next")
            self.assertIn("Reply style", self.context(r))

    def test_old_lock_record_is_migrated(self):
        folder = os.path.join(self.home, ".claude", "session_guard")
        with open(os.path.join(folder, "s1.json"), "w") as fh:
            json.dump({"locked": True, "reason": "finish session", "ids": ["msg_a"]}, fh)
        r = self.run_hook("UserPromptSubmit", "s1", self.transcript("t", 50_000), "hi")
        self.assertIn("finish session", r["reason"])
        with open(os.path.join(folder, "s1.json")) as fh:
            self.assertIn("finish", json.load(fh)["locks"])

    # --- Session Follow (quota) ----------------------------------------------
    def follow(self, name, percent, resets_in, read_ago=0):
        folder = os.path.join(self.home, ".claude", "session_guard", "session_follow")
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, name + ".json"), "w") as fh:
            json.dump({"used_percent": percent, "resets_at": time.time() + resets_in,
                       "read_at": time.time() - read_ago, "source": "test"}, fh)

    def test_quota_stop_then_lock_then_reset(self):
        self.follow("five_hour", 91, 2 * 3600)
        t = self.transcript("t", 50_000)
        r = self.run_hook("UserPromptSubmit", "s1", t, "next task")
        self.assertIn("QUOTA STOP", self.context(r))
        blocked = self.run_hook("UserPromptSubmit", "s1", t, "hi")
        self.assertEqual(blocked["decision"], "block")
        self.assertIn("Quota 91% (5-hour)", blocked["reason"])
        self.follow("five_hour", 3, 5 * 3600)  # a newer, lower reading: the window reset
        self.assertNotIn("decision", self.run_hook("UserPromptSubmit", "s1", t, "hi"))

    def test_quota_lock_never_clears_finish_lock(self):
        self.follow("weekly", 99, 3 * 3600)
        t = self.transcript("t", 50_000)
        self.run_hook("UserPromptSubmit", "s1", t, "go")
        self.follow("weekly", 2, 7 * 86400)
        self.run_hook("UserPromptSubmit", "s1", t, "finish session")
        self.assertIn("finish session", self.run_hook("UserPromptSubmit", "s1", t, "hi")["reason"])

    def test_quota_near_reset_does_not_stop(self):
        self.follow("five_hour", 95, 30 * 60)
        self.assertEqual(self.run_hook("UserPromptSubmit", "s1", self.transcript("t", 50_000), "go"), {})

    def test_new_session_over_quota_is_locked_at_once(self):
        self.follow("five_hour", 92, 2 * 3600)
        root = self.project("fresh")
        r = self.run_hook("SessionStart", "s1", cwd=root)
        self.assertIn("QUOTA LOCK", self.context(r))
        self.assertEqual(os.listdir(root), [])  # no review, no files
        self.assertEqual(self.run_hook("UserPromptSubmit", "s1", self.transcript("t", 50_000), "hi", cwd=root)["decision"], "block")

    def test_quota_turn_stopped_after_grace(self):
        self.follow("five_hour", 93, 2 * 3600)
        t = self.transcript("t", 50_000)
        self.assertIn("QUOTA STOP", self.context(self.run_hook("PostToolUse", "s1", t)))
        self.assertEqual(self.run_hook("PostToolUse", "s1", t), {})  # within the 15 minutes
        path = os.path.join(self.home, ".claude", "session_guard", "s1.json")
        with open(path) as fh:
            state = json.load(fh)
        state["locks"]["quota"]["at"] -= 16 * 60
        with open(path, "w") as fh:
            json.dump(state, fh)
        self.assertIs(self.run_hook("PostToolUse", "s1", t)["continue"], False)

    def test_desktop_usage_tool_result_is_read(self):
        result = {"plan": {"status": "ok", "windows": [
            {"label": "5-hour limit", "percentUsed": 12, "resetsAt": "2099-01-01T09:10:00.934Z"},
            {"label": "Weekly · all models", "percentUsed": 58, "resetsAt": "2099-01-03T00:00:00.934Z"},
            {"label": "Weekly · Opus", "percentUsed": 99, "resetsAt": "2099-01-03T00:00:00.934Z"}]}}
        self.run_hook("PostToolUse", "s1", self.transcript("t", 50_000), tool_name="mcp__ccd_session_mgmt__get_usage",
                      tool_response=[{"type": "text", "text": json.dumps(result)}])
        folder = os.path.join(self.home, ".claude", "session_guard", "session_follow")
        with open(os.path.join(folder, "weekly.json")) as fh:
            self.assertEqual(json.load(fh)["used_percent"], 58)
        with open(os.path.join(folder, "five_hour.json")) as fh:
            self.assertEqual(json.load(fh)["source"], "desktop")

    def test_desktop_asks_for_reading_when_old(self):
        self.env["CLAUDE_CODE_ENTRYPOINT"] = "claude-desktop"
        t = self.transcript("t", 50_000)
        self.assertIn("QUOTA READING", self.context(self.run_hook("UserPromptSubmit", "s1", t, "go")))
        self.follow("five_hour", 40, 3 * 3600)
        self.assertEqual(self.run_hook("UserPromptSubmit", "s1", t, "go"), {})
        self.follow("five_hour", 87, 3 * 3600)  # high: every prompt
        self.assertIn("QUOTA READING", self.context(self.run_hook("UserPromptSubmit", "s1", t, "go")))
        self.env.pop("CLAUDE_CODE_ENTRYPOINT")
        self.assertEqual(self.run_hook("UserPromptSubmit", "s1", t, "go"), {})  # terminal: the status line reads it

    # --- status line ----------------------------------------------------------
    def test_other_status_line_is_left_alone(self):
        path = os.path.join(self.home, ".claude", "settings.json")
        original = {"statusLine": {"type": "command", "command": "echo other-tool"}, "model": "x"}
        with open(path, "w") as fh:
            json.dump(original, fh)
        self.assertIn("another status line", self.cli("--install-statusline"))
        self.assertIn("no Session Guard", self.cli("--uninstall-statusline"))
        with open(path) as fh:
            self.assertEqual(json.load(fh), original)

    def test_statusline_alone_shows_quota(self):
        self.assertIn("installed", self.cli("--install-statusline"))
        self.assertIn("already", self.cli("--install-statusline"))
        data = {"rate_limits": {"five_hour": {"used_percentage": 7, "resets_at": time.time() + 3600}}}
        r = subprocess.run([sys.executable, "-I", SCRIPT, "--statusline"], input=json.dumps(data).encode(),
                           capture_output=True, env=self.env)
        self.assertIn(b"5-hour 7%", r.stdout)
        self.assertIn("removed", self.cli("--uninstall-statusline"))

    # --- similar files (item 8) -----------------------------------------------
    def test_similar_files_need_review(self):
        root = self.project("agents", {"AGENTS.md": "- keep notes", "HANDOFF.md": "where we stopped", "app.py": "x"})
        self.assertIn("PROJECT REVIEW PENDING", self.context(self.run_hook("SessionStart", "s1", cwd=root)))
        report = self.cli("--scan", root)
        self.assertIn("AGENTS.md  [same job", report)
        self.assertIn("HANDOFF.md  [same job", report)
        self.assertNotIn("app.py", report)
        self.assertIn("docs/STATUS.md", self.cli("--create-files", root))
        self.assertTrue(os.path.exists(os.path.join(root, "docs", "STATUS_ARCHIVE.md")))

    # --- notices in the app's language ------------------------------------------
    def app_language(self, locale):
        self.env["CLAUDE_CODE_ENTRYPOINT"] = "claude-desktop"
        folder = os.path.join(self.home, "local", "Claude-Data", "claude_x")
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "config.json"), "w") as fh:
            json.dump({"locale": locale, "other": 1}, fh)

    def finished_block(self, sid):
        t = self.transcript(sid, 50_000, msg_ids=(sid,))
        self.run_hook("UserPromptSubmit", sid, t, "finish session")
        return self.run_hook("UserPromptSubmit", sid, t, "hi")["reason"]

    def test_plugin_turkish_translation_is_complete(self):
        tr = json.loads(self.read(ROOT, "plugins", "session-guard", "messages", "tr.json"))
        catalog = json.loads(self.cli("--print-messages").split("\n", 1)[1])
        self.assertEqual(sorted(tr), sorted(catalog))
        for name, text in tr.items():
            self.assertEqual(sorted(re.findall(r"%\((\w+)\)[sd]", text)), sorted(re.findall(r"%\((\w+)\)[sd]", catalog[name])), name)

    def test_language_chosen_once_at_setup(self):
        self.app_language("tr")
        self.assertIn("notice language: tr; translation complete", self.cli("--set-language"))
        self.assertEqual(self.finished_block("a"), "Session Guard: Oturum kapandı (finish session). Yeni bir oturum açın.")
        self.follow("five_hour", 92, 2 * 3600)
        self.run_hook("UserPromptSubmit", "q", self.transcript("q", 50_000, msg_ids=("q",)), "go")
        self.assertIn("Kota %92 (5 saatlik)", self.run_hook("UserPromptSubmit", "q", self.transcript("q", 50_000, msg_ids=("q",)), "go")["reason"])
        self.follow("five_hour", 10, 2 * 3600)
        self.app_language("fr")  # the app setting is never checked again
        self.assertIn("Oturum kapandı", self.finished_block("b"))
        self.env.pop("CLAUDE_CODE_ENTRYPOINT")  # terminal: English
        self.assertIn("Session closed (finish session)", self.finished_block("c"))

    def test_user_asks_for_another_language(self):
        self.app_language("tr")
        self.cli("--set-language")
        self.assertIn("without a translation", self.cli("--set-language", "fr"))  # no French yet: English
        self.assertIn("Session closed (finish session)", self.finished_block("a"))
        path = os.path.join(self.home, "fr.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"closed_finish": "Session fermée (finish session). Ouvrez une nouvelle session.",
                       "hard_limit": "Contexte trop grand."}, fh)  # placeholder dropped: rejected
        self.assertIn("rejected (placeholders changed): hard_limit", self.cli("--set-messages", path))
        self.assertEqual(self.finished_block("b"), "Session Guard: Session fermée (finish session). Ouvrez une nouvelle session.")
        self.assertIn("hard limit", self.run_hook("UserPromptSubmit", "z", self.transcript("z", 410_000, msg_ids=("z",)), "go")["reason"])
        self.assertIn("without a translation for the language fr", self.context(self.run_hook("SessionStart", "s4")))
        self.assertIn("English", self.cli("--set-language", "english"))
        self.assertIn("Session closed", self.finished_block("c"))

    def test_mac_app_language_is_found(self):
        self.env["CLAUDE_CODE_ENTRYPOINT"] = "claude-desktop"
        folder = os.path.join(self.home, "Library", "Application Support", "Claude")
        os.makedirs(folder)
        with open(os.path.join(folder, "config.json"), "w") as fh:
            json.dump({"locale": "tr"}, fh)
        self.assertIn("notice language: tr", self.cli("--set-language"))

    def test_english_stores_nothing(self):
        self.app_language("en-US")
        self.assertIn("English", self.cli("--set-language"))
        self.env.pop("LOCALAPPDATA")  # app settings not found: Claude asks the user
        self.assertIn("Which language would you like session-guard notices in?", self.cli("--set-language"))
        path = os.path.join(self.home, "x.json")
        with open(path, "w") as fh:
            json.dump({"declined": "x"}, fh)
        self.assertIn("not stored", self.cli("--set-messages", path))

    def test_cloud_asks_and_keeps_language_in_repo(self):
        repo = self.project("repo")
        subprocess.run([sys.executable, "-I", SETUP, repo], capture_output=True, env=self.env, check=True)
        shutil.rmtree(os.path.join(self.home, ".claude"))
        cloud = dict(self.env, CLAUDE_CODE_REMOTE="true", CLAUDE_PROJECT_DIR=repo)
        copy = os.path.join(repo, ".claude", "session-guard", "session_guard.py")
        run = lambda *a: subprocess.run([sys.executable, "-I", copy, "--project", *a], capture_output=True, env=cloud).stdout.decode()
        self.assertIn("Which language would you like session-guard notices in?", run("--set-language"))
        self.assertIn("translation complete", run("--set-language", "tr"))  # the plugin's Turkish was copied along
        self.assertTrue(os.path.exists(os.path.join(repo, ".claude", "session-guard", "language.json")))
        start = json.dumps({"hook_event_name": "SessionStart", "session_id": "n", "source": "startup"}).encode()
        subprocess.run([sys.executable, "-I", copy, "--project"], input=start, capture_output=True, env=cloud)
        for prompt in ("finish session", "hi"):
            data = json.dumps({"hook_event_name": "UserPromptSubmit", "session_id": "n", "prompt": prompt}).encode()
            r = subprocess.run([sys.executable, "-I", copy, "--project"], input=data, capture_output=True, env=cloud)
        self.assertIn("Oturum kapandı", json.loads(r.stdout)["reason"])


    # --- one home per project; control project ----------------------------------
    def test_without_control_project_off_on_computer(self):
        shutil.rmtree(self.executive)
        self.assertIn("session-guard-executive", self.run_hook("SessionStart", "s1")["systemMessage"])
        self.assertEqual(self.run_hook("UserPromptSubmit", "s1", self.transcript("t", 410_000), "go"), {})
        self.assertIn("not recorded", self.cli("--set-executive", self.project("elsewhere")))

    def test_computer_project_gets_cloud_guard(self):
        root = self.project("app", {".claude/settings.json": json.dumps({"permissions": {"allow": ["Bash(ls)"]}})})
        os.makedirs(os.path.join(root, ".git"))
        self.cli("--setup", "approved", root)
        self.assertIn("cloud guard", self.context(self.run_hook("SessionStart", "s1", cwd=root)))
        settings = json.loads(self.read(root, ".claude", "settings.json"))
        self.assertEqual(settings["permissions"]["allow"], ["Bash(ls)"])
        command = settings["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"]
        self.assertNotIn("cloud guard", self.context(self.run_hook("SessionStart", "s2", cwd=root)))  # added once
        self.assertNotIn("Stop:", self.cli("--scan", root))  # the guard is not a conflict
        here = subprocess.run(["bash", "-c", command], capture_output=True, env=self.env)
        self.assertEqual(here.stdout, b"")  # on the computer it does nothing
        cloud = subprocess.run(["bash", "-c", command], capture_output=True, env=dict(self.env, CLAUDE_CODE_REMOTE="true"))
        self.assertEqual(json.loads(cloud.stdout)["decision"], "block")
        r = subprocess.run([sys.executable, "-I", SETUP, "--reviewed", root], capture_output=True, env=self.env)
        self.assertEqual(r.returncode, 1)  # no cloud copy in a computer project
        self.assertFalse(os.path.exists(os.path.join(root, ".claude", "session-guard")))

    def test_cloud_project_blocked_on_computer(self):
        repo = self.project("cloudrepo", {"CLAUDE.md": "rules"})
        os.makedirs(os.path.join(repo, ".git"))
        subprocess.run([sys.executable, "-I", SETUP, "--reviewed", repo], capture_output=True, env=self.env, check=True)
        self.assertIn("cloud project", self.run_hook("SessionStart", "s1", cwd=repo)["systemMessage"])
        self.assertIn("cloud project", self.run_hook("UserPromptSubmit", "s1", self.transcript("t", 50_000), "hi", cwd=repo)["reason"])
        self.app_language("tr")
        self.cli("--set-language")
        self.assertIn("This repository is a cloud project", self.run_hook("UserPromptSubmit", "s2", self.transcript("t", 50_000), "hi", cwd=repo)["reason"])  # always English
        self.assertFalse(os.path.exists(os.path.join(repo, ".claude", "settings.json")) and
                         "session-guard-home" in self.read(repo, ".claude", "settings.json"))
        shutil.rmtree(os.path.join(self.home, ".claude"))  # a computer without the plugin: the repo copy blocks
        data = json.dumps({"hook_event_name": "UserPromptSubmit", "session_id": "x", "prompt": "hi"}).encode()
        copy = os.path.join(repo, ".claude", "session-guard", "session_guard.py")
        r = subprocess.run([sys.executable, "-I", copy, "--project"], input=data, capture_output=True,
                           env=dict(self.env, CLAUDE_PROJECT_DIR=repo))
        self.assertIn("cloud project", json.loads(r.stdout)["reason"])


if __name__ == "__main__":
    unittest.main()
