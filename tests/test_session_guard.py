"""Tests for Session Guard. Run: python -m unittest discover -s tests

Each test runs the hook script as Claude Code would (JSON on stdin) against a
fake transcript, with HOME pointed at a temporary folder.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
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

    def test_finished_subagent_is_not_pending(self):
        launch = {"type": "assistant", "timestamp": stamp(90), "message": {"id": "msg_b", "content": [
            {"type": "tool_use", "id": "toolu_2", "name": "Agent", "input": {"prompt": "x"}}]}}
        result = {"type": "user", "timestamp": stamp(90), "message": {"content": [
            {"type": "tool_result", "tool_use_id": "toolu_2", "content": "Async agent launched. agentId: a1b2c3"}]}}
        back = {"type": "user", "timestamp": stamp(85), "message": {"content": '<agent-message from="a1b2c3">report</agent-message>'}}
        r = self.run_hook("UserPromptSubmit", "s1", self.transcript("t", 200_000, 90, extra=[launch, result, back]), "next")
        self.assertIn("BREAK RULE", self.context(r))

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
        os.makedirs(os.path.join(self.home, "docs"))
        with open(os.path.join(self.home, "docs", "STATUS.md"), "w", encoding="utf-8") as fh:
            fh.write("# STATUS\nopen item")
        ctx = self.context(self.run_hook("SessionStart", "s1"))
        self.assertIn("Reply style", ctx)
        self.assertIn("open item", ctx)

    def test_bad_input_never_fails(self):
        r = subprocess.run([sys.executable, "-I", SCRIPT], input=b"not json", capture_output=True, env=self.env)
        self.assertEqual(r.returncode, 0)

    def test_cloud_setup_is_idempotent_and_quiet_with_plugin(self):
        repo = os.path.join(self.home, "repo")
        os.makedirs(os.path.join(repo, ".claude"))
        with open(os.path.join(repo, ".claude", "settings.json"), "w") as fh:
            json.dump({"permissions": {"allow": ["Bash(ls)"]}}, fh)
        for _ in range(2):
            subprocess.run([sys.executable, "-I", SETUP, repo], capture_output=True, env=self.env, check=True)
        with open(os.path.join(repo, ".claude", "settings.json")) as fh:
            settings = json.load(fh)
        self.assertEqual(settings["permissions"]["allow"], ["Bash(ls)"])
        self.assertEqual(len(settings["hooks"]["UserPromptSubmit"]), 1)
        copy = os.path.join(repo, ".claude", "session-guard", "session_guard.py")
        data = json.dumps({"hook_event_name": "UserPromptSubmit", "session_id": "s", "prompt": "hi"}).encode()
        alone = subprocess.run([sys.executable, "-I", copy, "--project"], input=data, capture_output=True, env=self.env)
        self.assertIn(b"Session Guard", alone.stdout)
        os.makedirs(os.path.join(self.home, ".claude", "plugins", "cache", "sg", "scripts"))
        shutil.copyfile(SCRIPT, os.path.join(self.home, ".claude", "plugins", "cache", "sg", "scripts", "session_guard.py"))
        quiet = subprocess.run([sys.executable, "-I", copy, "--project"], input=data, capture_output=True, env=self.env)
        self.assertEqual(quiet.stdout, b"")


if __name__ == "__main__":
    unittest.main()
