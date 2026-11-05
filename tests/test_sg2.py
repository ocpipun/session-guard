# SESSION-GUARD: 2.0 | component: tests-sg2 | version: 2.0.0
"""Tests for Session Guard 2.0. Run: python -m unittest discover -s tests

Every test runs the real script in a subprocess (as Claude Code runs hooks: JSON on stdin) with HOME pointed at a
temporary folder, so the real ~/.claude is never touched.
"""
import hashlib
import json
import os
import re
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


def in_store(root, change):
    """Bulk work straight through the store module (one subprocess per item would be slow)."""
    if os.path.join(PLUGIN, "scripts") not in sys.path:
        sys.path.insert(0, os.path.join(PLUGIN, "scripts"))
    from sg2 import store
    return store.mutate(root, lambda d: change(store, d))


def unique(prefix, n):
    return "%s %d %s" % (prefix, n, hashlib.md5(("%s%d" % (prefix, n)).encode()).hexdigest()[:10])


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

    def test_main_task_limit_ten_per_working_agent(self):
        _, a = self.start("la", self.root)
        _, b = self.start("lb", self.root)
        new = lambda sid, title: self.sg("task", "new", "--title", title, "--approval", "ok", "--session", sid,
                                         "--root", self.root)
        for n in range(10):
            self.assertIn("created", new(a, unique("work item", n)))
        self.assertIn("limit 10", new(a, "the eleventh task"))
        self.assertIn("created", new(b, "the second agent's task"))       # two agents working at once: 20
        self.assertIn("normal 11/20 · urgent 0/6", self.text(self.root, "docs", "STATUS.md"))
        for n in range(6):  # subtasks have no limit
            self.sg("task", "new", "--title", "sub%d" % n, "--parent", "TASK-002", "--session", a, "--root",
                    self.root, ok=True)

    def test_no_status_line_limit(self):
        sids = ["S-20261104-%06x" % n for n in range(1, 5)]

        def fill(store, d):
            for sid in sids:
                store.register_session(d, sid, None, "computer")
                for n in range(10):
                    store.new_task(d, sid, unique(sid, n), approval="ok")
        in_store(self.root, fill)
        self.assertIn("normal 40/40", self.text(self.root, "docs", "STATUS.md"))
        self.assertGreater(len(self.text(self.root, "docs", "STATUS.md").splitlines()), 50)
        out = self.hook("UserPromptSubmit", "nl", cwd=self.root, prompt="go on")
        self.assertNotIn("LINES", json.dumps(out))                         # no refusal for a long STATUS
        self.sg("migrate", "verify", self.root, ok=True)

    def test_urgent_first_and_no_new_task_until_done(self):
        self.t("new", "--title", "normal work", "--approval", "ok", ok=True)                          # TASK-002
        self.assertIn("--reason", self.t("new", "--title", "U", "--kind", "urgent", "--approval", "ok", ok=False))
        self.t("new", "--title", "production is down", "--kind", "urgent", "--approval", "ok", "--reason", "outage",
               ok=True)                                                                               # TASK-003
        status = self.text(self.root, "docs", "STATUS.md")
        self.assertLess(status.index("TASK-003"), status.index("TASK-002"))           # urgent at the top
        self.assertIn("URGENT FIRST", status)
        self.assertIn("YOUR URGENT TASK IS ACTIVE (TASK-003)", self.t("new", "--title", "D", "--approval", "ok",
                                                                      ok=False))
        self.assertIn("YOUR URGENT TASK IS ACTIVE", self.t("new", "--title", "Q", "--queue", "--approval", "ok",
                                                           ok=False))
        self.assertIn("IDEA-001", self.sg("idea", "add", "record it for later", "--session", S1, "--root", self.root,
                                          ok=True))                                   # ideas are still recorded
        self.assertIn("YOUR URGENT TASK IS ACTIVE", self.sg("idea", "promote", "IDEA-001", "--approval", "ok",
                                                            "--session", S1, "--root", self.root, ok=False))
        self.t("new", "--title", "fix the database", "--kind", "urgent", "--approval", "ok", "--reason", "data loss",
               ok=True)
        self.t("new", "--title", "restart the job runner", "--kind", "urgent", "--approval", "ok", "--reason",
               "jobs stuck", ok=True)
        self.assertIn("3 urgent", self.t("new", "--title", "U4", "--kind", "urgent", "--approval", "ok", "--reason",
                                         "x", ok=False))
        self.t("new", "--title", "another agent's work", "--approval", "ok", sid=S2, ok=True)    # it goes on
        self.t("new", "--title", "check the logs", "--parent", "TASK-003", ok=True)   # the urgent work goes on
        for tid in ("TASK-003-1", "TASK-003", "TASK-004", "TASK-005"):
            self.t("done", tid, "--summary", "finished", ok=True)
        self.t("new", "--title", "D", "--approval", "ok", ok=True)

    def test_subagent_gets_only_a_subtask_of_an_owned_task(self):
        _, a = self.start("sa", self.root)
        self.sg("task", "new", "--title", "build the parser", "--approval", "ok", "--session", a, "--root", self.root,
                ok=True)                                                                          # TASK-002
        launch = lambda prompt, kind="general-purpose": self.hook(
            "PreToolUse", "sa", cwd=self.root, tool_name="Agent",
            tool_input={"prompt": prompt, "description": "x", "subagent_type": kind})
        denied = lambda out: (out.get("hookSpecificOutput") or {}).get("permissionDecision") == "deny"
        self.assertTrue(denied(launch("Write a new CSV exporter for the user")))           # independent work
        self.assertTrue(denied(launch("Work on TASK-002")))                                # a main task, not a subtask
        self.sg("task", "new", "--title", "parse dates", "--parent", "TASK-002", "--session", a, "--root", self.root,
                ok=True)                                                                          # TASK-002-1
        self.assertFalse(denied(launch("Your subtask is TASK-002-1: parse dates")))
        self.assertTrue(denied(launch("Your subtask is TASK-009-1")))                      # not this session's
        self.assertFalse(denied(launch("Read https://example.com", "web-fetch")))
        sg = 'python "%s" ' % SCRIPT
        bash = lambda cmd, **extra: self.hook("PreToolUse", "sa", cwd=self.root, tool_name="Bash",
                                              tool_input={"command": cmd}, **extra)
        self.assertTrue(denied(bash(sg + 'task new --title "other work" --approval ok', agent_id="ag1",
                                    agent_type="general-purpose")))
        self.assertTrue(denied(bash(sg + "task start TASK-003", agent_id="ag1")))
        self.assertFalse(denied(bash(sg + 'task new --title "dates in UTC" --parent TASK-002-1', agent_id="ag1")))
        self.assertFalse(denied(bash(sg + 'handoff write TASK-002-1 --technical "where it stopped"', agent_id="ag1")))
        self.assertFalse(denied(bash(sg + 'task new --title "other work" --approval ok')))    # the session itself
        self.assertEqual(self.store(self.root)["sessions"][a]["state"], "active")
        _, other = self.start("so", self.root)
        self.assertTrue(denied(bash(sg + 'task claim TASK-002 --session %s --takeover yes' % other)))  # borrowed id
        self.assertFalse(denied(bash(sg + 'task note TASK-002 --text x --session %s' % a)))     # its own id

    def test_child_session_gets_its_own_id_and_goes_on_when_the_parent_stops(self):
        _, a = self.start("pa", self.root)
        _, c = self.start("ch", self.root)
        out = self.hook("UserPromptSubmit", "ch", cwd=self.root,
                        prompt="Build the exporter.\nSG-PARENT: %s independent\nThe user said: yes, do it" % a)
        child = a + "-I1"
        self.assertIn("CHILD SESSION: your SESSION-ID is %s" % child, self.ctx(out))
        data = self.store(self.root)
        self.assertEqual(data["sessions"][child]["parent"], a)
        self.assertIn(child, data["sessions"][a]["children"])
        self.assertEqual(data["sessions"][c]["renamed_to"], child)
        self.assertIn("owner " + child, self.sg("task", "new", "--title", "build the exporter", "--approval",
                                                 "yes, do it", "--session", c, "--root", self.root, ok=True))
        self.hook("UserPromptSubmit", "pa", cwd=self.root, prompt="finish session")
        self.assertEqual(self.store(self.root)["sessions"][a]["stopped"]["reason"], "finish: finish session")
        out = self.hook("PostToolUse", "ch", cwd=self.root, tool_name="Bash")
        self.assertIn("PARENT SESSION %s HANDS OFF / STOPPED" % a, self.ctx(out))
        self.assertIn("INDEPENDENT child session agent", self.ctx(out))        # its own main task: it goes on
        self.assertNotIn("PARENT SESSION", self.ctx(self.hook("PostToolUse", "ch", cwd=self.root, tool_name="Bash")))
        self.assertNotIn("decision", self.hook("UserPromptSubmit", "ch", cwd=self.root, prompt="go on"))

    def test_bound_child_hands_off_before_its_parent_and_is_locked(self):
        _, a = self.start("pb", self.root)
        self.sg("task", "new", "--title", "release 2.0", "--approval", "ok", "--session", a, "--root", self.root,
                ok=True)                                                                          # TASK-002
        _, c = self.start("cb", self.root)
        self.hook("UserPromptSubmit", "cb", cwd=self.root, prompt="Test the installer.\nSG-PARENT: %s bound" % a)
        child = a + "-B1"
        self.sg("task", "new", "--title", "test the installer", "--parent", "TASK-002", "--session", c, "--root",
                self.root, ok=True)                                                               # TASK-002-1, bound
        hw = lambda tid, sid: self.sg("handoff", "write", tid, "--technical", "where it stopped", "--session", sid,
                                      "--root", self.root)
        self.assertIn("have not written their hand-offs yet: " + child, hw("TASK-002", a))   # not before the child
        out = self.sg("handoff", "children", "--session", a, "--root", self.root, ok=True)
        self.assertIn("BOUND %s: NOT READY, hand-off missing for TASK-002-1" % child, out)
        self.assertIn("PARENT HANDING OFF (%s)" % a, out)                                  # the message to send
        out = self.hook("UserPromptSubmit", "cb", cwd=self.root, prompt=out.split('"')[1])  # the child gets it
        self.assertIn("BOUND child session agent", self.ctx(out))
        self.assertIn("hand-off", hw("TASK-002-1", c))
        self.assertEqual(self.hook("UserPromptSubmit", "cb", cwd=self.root, prompt="go on").get("decision"),
                         "block")                                                          # locked after its hand-off
        self.assertIn("ready (all hand-offs written)", self.sg("handoff", "children", "--session", a, "--root",
                                                               self.root, ok=True))
        self.assertIn("prepared", hw("TASK-002", a))
        self.assertIn("Child session agents: %s bound (tasks TASK-002-1" % child,
                      self.sg("task", "show", "TASK-002", "--root", self.root, ok=True))

    def test_child_kinds_never_take_each_others_work(self):
        _, a = self.start("pk", self.root)
        sg = lambda sid, *args, ok=True: self.sg(*args, "--session", sid, "--root", self.root, ok=ok)
        sg(a, "task", "new", "--title", "release 2.0", "--approval", "ok")                      # TASK-002, parent's
        _, n = self.start("nk", self.root)
        out = self.hook("UserPromptSubmit", "nk", cwd=self.root, prompt="Help.\nSG-PARENT: %s" % a)
        self.assertIn("has no kind", self.ctx(out))                                   # no kind: not a child
        self.assertNotIn("renamed_to", self.store(self.root)["sessions"][n])
        _, b = self.start("bk", self.root)
        self.hook("UserPromptSubmit", "bk", cwd=self.root, prompt="Test it.\nSG-PARENT: %s bound" % a)
        _, i = self.start("ik", self.root)
        self.hook("UserPromptSubmit", "ik", cwd=self.root, prompt="Own work.\nSG-PARENT: %s INDEPENDENT" % a)
        data = self.store(self.root)["sessions"]
        self.assertEqual((data[a + "-B1"]["kind"], data[a + "-I1"]["kind"]), ("bound", "independent"))
        self.assertIn("BOUND child session agent", sg(b, "task", "new", "--title", "my own", "--approval", "ok",
                                                      ok=False))                          # no main task
        sg(b, "task", "new", "--title", "test the installer", "--parent", "TASK-002")      # TASK-002-1
        self.assertIn("INDEPENDENT child session agent", sg(i, "task", "new", "--title", "a part", "--parent",
                                                             "TASK-002", ok=False))       # not the parent's work
        self.assertIn("INDEPENDENT child session agent", sg(i, "task", "claim", "TASK-002", "--takeover", "ok",
                                                             ok=False))
        sg(i, "task", "new", "--title", "export reports", "--approval", "yes")             # TASK-003, its own
        self.assertIn("BOUND child session agent", sg(b, "task", "new", "--title", "x", "--parent", "TASK-003",
                                                      ok=False))                          # not its parent's work
        sg(i, "task", "new", "--title", "csv part", "--parent", "TASK-003")                # its own subtask

    def test_new_work_check_lists_all_work_and_finds_pre20_by_keywords(self):
        self.t("new", "--title", "write the user guide", "--approval", "ok", ok=True)            # TASK-002 running
        self.t("new", "--title", "add a dark theme", "--queue", "--approval", "ok", ok=True)     # TASK-003 queued
        self.t("new", "--title", "export reports as csv", "--approval", "ok", ok=True)           # TASK-004
        self.t("done", "TASK-004", "--summary", "finished", ok=True)
        with open(os.path.join(self.root, "docs", "COMPLETED_TASKS.md"), "a", encoding="utf-8") as fh:
            fh.write("- 2026-11-01 [SESSION-GUARD: 2.0] PRJ-x PRE20-DONE-001 · login sayfası düzeltildi · done "
                     "before Session Guard 2.0\n")
        out = self.sg("task", "check", "--title", "login sayfasını yeniden düzelt", "--title", "add a dark theme",
                      "--title", "reports exported to csv files", "--root", self.root, ok=True)
        self.assertIn("limits (1 agent: 10 normal + 3 urgent each): active normal 1/10 · urgent 0/3", out)
        self.assertIn("NEAR COPY of TASK-003", out)
        self.assertIn("- TASK-002 · write the user guide · open", out)             # all running work in full
        self.assertIn("- TASK-003 · P2 · add a dark theme", out)                    # all queued work
        self.assertIn("- PRE20-IDEA-001 · later idea", out)                         # all ideas
        self.assertIn("- PRE20-DONE-001 · login sayfası düzeltildi", out)           # pre-2.0 by keywords
        self.assertIn("- TASK-004 · export reports as csv · done", out)             # finished 2.0 work by keywords
        self.assertNotIn("URGENT TASK IS ACTIVE", out)
        self.assertIn("give every new item", self.sg("task", "check", "--root", self.root, ok=False))

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
        self.assertIn("open subtasks", self.t("done", "TASK-002", "--summary", "finished", ok=False))
        self.t("done", "TASK-002-1", "--summary", "finished", ok=True)
        self.assertEqual(self.store(self.root)["tasks"]["TASK-002"]["status"], "open")
        self.t("done", "TASK-002", "--summary", "finished", ok=True)
        self.assertIn("TASK-002 · A · done", self.text(self.root, "docs", "COMPLETED_TASKS.md"))

    def test_finished_task_reopened_gets_new_id(self):
        self.t("new", "--title", "A", "--approval", "ok", ok=True)
        self.t("done", "TASK-002", "--summary", "finished", ok=True)
        self.assertIn("reopen", self.t("claim", "TASK-002", ok=False))
        self.assertIn("TASK-003", self.t("reopen", "TASK-002", "--title", "A again", "--approval", "ok", ok=True))
        data = self.store(self.root)
        self.assertIn({"task": "TASK-002", "relation": "reopens"}, data["tasks"]["TASK-003"]["related"])
        self.assertIn({"task": "TASK-003", "relation": "reopened-by"}, data["tasks"]["TASK-002"]["related"])

    def test_backlog_idea_and_promotion(self):
        self.assertIn("IDEA-001", self.sg("idea", "add", "dark mode", "--session", S1, "--root", self.root))
        self.assertIn("### IDEA-001 · dark mode", self.text(self.root, "docs", "IDEA_TASKS.md"))
        self.assertNotIn("dark mode", self.text(self.root, "docs", "TASK_BACKLOG.md"))
        self.assertIn("approval", self.sg("idea", "promote", "IDEA-001", "--session", S1, "--root", self.root,
                                          ok=False))
        self.sg("idea", "promote", "IDEA-001", "--approval", "ok", "--session", S1, "--root", self.root, ok=True)

    def test_handoff_once_then_claim_archives_and_locks_source(self):
        _, a = self.start("ha", self.root)
        self.sg("task", "new", "--title", "A", "--approval", "ok", "--session", a, "--root", self.root, ok=True)
        w = lambda: self.sg("handoff", "write", "TASK-002", "--technical", "run x", "--pending", "y", "--session", a,
                            "--root", self.root)
        self.assertIn("prepared", w())
        self.assertIn("unchanged", w())
        self.assertIn("Hand-offs waiting (1)", self.text(self.root, "docs", "STATUS.md"))
        self.assertIn("session note first", self.sg("handoff", "publish", "--session", a, "--root", self.root, ok=False))
        self.sg("handoff", "note", "--text", "A half done.", "--session", a, "--root", self.root, ok=True)
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

    def test_session_notes_agents_line_and_outdated_note_to_archive(self):
        status = lambda: self.text(self.root, "docs", "STATUS.md")
        arg = lambda sid: ("--session", sid, "--root", self.root)
        _, a = self.start("na", self.root)
        self.sg("task", "new", "--title", "A", "--approval", "ok", *arg(a), ok=True)               # TASK-002
        self.sg("handoff", "write", "TASK-002", "--technical", "x", *arg(a), ok=True)
        self.sg("handoff", "note", "--text", "Note of A.", *arg(a), ok=True)
        self.sg("handoff", "publish", *arg(a), ok=True)
        _, c = self.start("nc", self.root)                                                        # a parallel agent
        self.sg("task", "new", "--title", "C", "--approval", "ok", *arg(c), ok=True)               # TASK-003
        self.sg("handoff", "write", "TASK-003", "--technical", "y", *arg(c), ok=True)
        self.sg("handoff", "note", "--text", "Note of C.", *arg(c), ok=True)
        self.assertIn("## Hand-off notes (2)", status())
        self.assertRegex(status(), r"- %s · \d{4}-\d\d-\d\d · tasks TASK-002 waiting · quota n/a: Note of A\." % a)
        out, b = self.start("nb", self.root)
        self.assertIn("propose first to finish one of these (recommend TASK-002", self.ctx(out))
        self.sg("task", "claim", "TASK-002", *arg(b), ok=True)
        self.assertRegex(status(), r"- Agents \(2\): %s \d\d-\d\d→\d\d-\d\d handed off · %s \d\d-\d\d→now" % (a, b))
        self.assertIn("tasks TASK-002 taken by %s" % b, status())                # kept until a newer note exists
        self.sg("handoff", "write", "TASK-002", "--technical", "z", *arg(b), ok=True)
        self.sg("handoff", "note", "--text", "Note of B.", *arg(b), ok=True)
        self.assertIn("## Hand-off notes (2)", status())                        # C's work still waits: C's note stays
        self.assertNotIn("Note of A.", status())
        self.assertIn("Note of C.", status())
        arch = self.text(self.root, "docs", "STATUS_ARCHIVE.md")
        self.assertIn("Hand-off note of %s (outdated by %s's note)" % (a, b), arch)
        self.assertIn("Note of A.", arch)
        self.assertIn("no hand-off of yours", self.sg("handoff", "note", "--text", "x", *arg(S2), ok=False))

    def test_completed_task_result_project_state_and_start_order(self):
        self.t("new", "--title", "A", "--approval", "ok", ok=True)
        self.assertIn("--summary", self.t("done", "TASK-002", ok=False))
        self.assertIn("project state note is for", self.sg("handoff", "note", "--project-state", "--text", "x",
                                                           "--session", S1, "--root", self.root, ok=False))
        out = self.t("done", "TASK-002", "--summary", "A works for every user.", ok=True)
        self.assertIn("ALL YOUR TASKS ARE FINISHED", out)
        self.assertIn("TASK-002 · A · done · result: A works for every user. · agents %s" % S1,
                      self.text(self.root, "docs", "COMPLETED_TASKS.md"))
        self.sg("handoff", "note", "--project-state", "--text", "A is finished; nothing is left.", "--session", S1,
                "--root", self.root, ok=True)
        self.assertIn("- PROJECT STATE by %s" % S1, self.text(self.root, "docs", "STATUS.md"))
        out, _ = self.start("ns", self.root)
        self.assertIn("No task waits to be taken over", self.ctx(out))
        self.assertIn("docs/TASK_BACKLOG.md, 0 queued", self.ctx(out))

    def test_finished_task_needs_no_handoff(self):
        self.t("new", "--title", "A", "--approval", "ok", ok=True)
        self.t("done", "TASK-002", "--summary", "finished", ok=True)
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

    def test_fork_keeps_long_mode_as_a_new_session_and_compaction_rearms_notices(self):
        root = self.migrated("fk")
        tr = self.transcript("fk", 1000)
        _, a = self.start("fa", root, tr)
        self.hook("UserPromptSubmit", "fa", tr, cwd=root, prompt="start long mode")
        out = self.hook("UserPromptSubmit", "fa", self.transcript("fk", 260000), cwd=root, prompt="go")
        self.assertIn("NOTICE (250k", self.ctx(out))
        big = self.transcript("fk", 558000)                       # the same conversation, now 558k
        out, b = self.start("fb", root, big, source="fork")
        self.assertNotEqual(a, b)                                  # a fork is its own session...
        self.assertIn("FORK of session %s" % a, self.ctx(out))
        rec = json.load(open(os.path.join(self.home, ".claude", "session_guard_v2", "sessions", "fb.json")))
        self.assertTrue(rec["long"])                               # ...with the same mode
        self.assertEqual(self.store(root)["sessions"][b]["parent"], a)
        out = self.hook("UserPromptSubmit", "fb", big, cwd=root, prompt="go on")
        self.assertNotIn("decision", out)                          # not hard-stopped at 558k (v1.4 was)
        out = self.hook("SessionStart", "fa", self.transcript("fk", 22000), cwd=root, source="compact")
        self.assertIn("COMPACTED", self.ctx(out))
        out = self.hook("UserPromptSubmit", "fa", self.transcript("fk", 255000), cwd=root, prompt="go")
        self.assertIn("NOTICE (250k", self.ctx(out))               # the notice fires again after compaction

    def conversation(self, name, n, context, minutes_ago=30, head=()):
        """A transcript with n assistant replies (ids msg_conv_<i>), the last one at the given context size; head:
        entries written first, as the desktop app does when it copies a conversation to a new id."""
        p = os.path.join(self.home, name + ".jsonl")
        with open(p, "w", encoding="utf-8") as fh:
            for e in head:
                fh.write(json.dumps(e) + "\n")
            for i in range(n):
                size = context if i == n - 1 else 1000 + i
                fh.write(json.dumps({"type": "assistant", "timestamp": stamp(minutes_ago - i * 0.1), "message": {
                    "id": "msg_conv_%d" % i, "usage": {"input_tokens": 2, "cache_creation_input_tokens": 1000,
                                                       "cache_read_input_tokens": size - 1002, "output_tokens": 5}}})
                         + "\n")
        return p

    def test_copied_conversation_found_from_its_messages_after_the_first_hook(self):
        """Seen 2026-11-05: the desktop app copied a long-mode conversation to a new id; the first hook ran before the
        copy was written, a new record was made, and 400k (normal mode) stopped it. Now the record keeps looking."""
        root = self.migrated("cp")
        tr = self.conversation("orig", 5, 200000)
        _, a = self.start("orig", root, tr)
        self.hook("UserPromptSubmit", "orig", tr, cwd=root, prompt="start long mode")
        self.sg("task", "new", "--title", "A", "--approval", "ok", "--session", a, "--root", root, ok=True)
        unfinished = os.path.join(self.home, "copy.jsonl")
        with open(unfinished, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"type": "queue-operation", "timestamp": stamp(0)}) + "\n")
        _, fresh = self.start("copy", root, unfinished)
        self.assertNotEqual(fresh, a)                             # nothing to match yet
        copied = self.conversation("copy", 8, 413000, head=[{"type": "custom-title"}])
        out = self.hook("PostToolUse", "copy", copied, cwd=root, tool_name="Bash")
        self.assertIn("SAME CONVERSATION", self.ctx(out))
        self.assertIn("SESSION-ID is %s again (mode LONG" % a, self.ctx(out))
        self.assertNotIn("continue", out)                         # not stopped at 413k: long mode kept
        out = self.hook("UserPromptSubmit", "copy", copied, cwd=root, prompt="go on")
        self.assertNotIn("decision", out)
        self.assertIn("Mode: LONG", self.ctx(out))
        data = self.store(root)
        self.assertEqual(data["sessions"][fresh]["renamed_to"], a)   # the id made meanwhile is an alias
        self.assertIn("copy", data["sessions"][a]["claude_ids"])
        self.assertEqual(data["tasks"]["TASK-002"]["owner"], a)
        self.sg("task", "note", "TASK-002", "--done", "x", "--session", fresh, "--root", root, ok=True)  # alias works

    def test_session_found_from_the_project_store_on_another_machine(self):
        """A cloud VM rebuilt, or ~/.claude lost: no machine record, but the project's store knows the conversation's
        first message ids and its long mode."""
        root = self.migrated("vm")
        tr = self.conversation("v1", 3, 5000)
        _, a = self.start("v1", root, tr)
        self.hook("UserPromptSubmit", "v1", tr, cwd=root, prompt="start long mode")
        self.assertEqual(self.store(root)["sessions"][a]["first_ids"][:1], ["msg_conv_0"])
        self.assertTrue(self.store(root)["sessions"][a]["long"])
        shutil.rmtree(os.path.join(self.home, ".claude", "session_guard_v2", "sessions"))
        later = self.conversation("v2", 6, 450000)
        out, b = self.start("v2", root, later, source="resume")
        self.assertEqual(b, a)
        out = self.hook("UserPromptSubmit", "v2", later, cwd=root, prompt="go on")
        self.assertNotIn("decision", out)                         # long mode came back with it: no 400k stop
        self.assertIn("Mode: LONG", self.ctx(out))

    def test_new_conversation_is_never_linked_to_a_later_copy(self):
        root = self.migrated("nl")
        tr = self.conversation("n1", 2, 3000)
        _, a = self.start("n1", root, tr)
        _, b = self.start("n2", root, tr, source="fork")          # a fork made later
        self.assertNotEqual(a, b)
        out = self.hook("PostToolUse", "n1", tr, cwd=root, tool_name="Bash")
        self.assertNotIn("SAME CONVERSATION", self.ctx(out))
        self.assertEqual(json.load(open(os.path.join(self.home, ".claude", "session_guard_v2", "sessions",
                                                     "n1.json")))["sg_id"], a)

    def test_long_mode_hands_off_at_800k_not_700k(self):
        root = self.project()
        self.start("h", root)
        self.hook("UserPromptSubmit", "h", self.transcript("h", 1000), cwd=root, prompt="start long mode")
        out = self.hook("UserPromptSubmit", "h", self.transcript("h", 710000), cwd=root, prompt="go")
        self.assertIn("NOTICE (700k", self.ctx(out))
        self.assertNotIn("Hand off", self.ctx(out))
        self.assertNotIn("SESSION MEASUREMENT", self.ctx(out))
        out = self.hook("PostToolUse", "h", self.transcript("h", 810000), cwd=root, tool_name="Bash")
        self.assertIn("Hand off every task you own now", self.ctx(out))
        self.assertIn("SESSION MEASUREMENT", self.ctx(out))

    def test_long_mode_refused_after_300k_handoff(self):
        root = self.project()
        self.start("r", root)
        out = self.hook("PostToolUse", "r", self.transcript("r", 310000), cwd=root, tool_name="Bash")
        self.assertIn("HAND-OFF (300k)", self.ctx(out))
        self.assertIn('report "%s"' % root, self.ctx(out))
        out = self.hook("UserPromptSubmit", "r", self.transcript("r", 312000), cwd=root, prompt="start long mode")
        self.assertEqual(out.get("decision"), "block")
        self.assertIn("Long mode cannot be switched on", out.get("reason"))
        out = self.hook("UserPromptSubmit", "r", self.transcript("r", 312000), cwd=root, prompt="what is X?")
        self.assertNotIn("decision", out)
        self.assertIn("Mode: normal", self.ctx(out))
        self.assertNotIn("SESSION MEASUREMENT", self.ctx(out))

    def test_finish_refused_after_handoff(self):
        root = self.project()
        self.start("n", root)
        self.hook("UserPromptSubmit", "n", self.transcript("n", 310000), cwd=root, prompt="go")
        out = self.hook("UserPromptSubmit", "n", self.transcript("n", 312000), cwd=root, prompt="finish session")
        self.assertEqual(out.get("decision"), "block")
        self.assertIn("hand-off was already prepared at 300k", out.get("reason"))
        self.assertNotIn("decision", self.hook("UserPromptSubmit", "n", self.transcript("n", 312000), cwd=root,
                                               prompt="what is X?"))
        out = self.hook("UserPromptSubmit", "n", self.transcript("n", 405000), cwd=root, prompt="finish session")
        self.assertIn("hard limit", out.get("reason"))
        self.start("g", root)
        self.hook("UserPromptSubmit", "g", self.transcript("g", 1000), cwd=root, prompt="start long mode")
        out = self.hook("UserPromptSubmit", "g", self.transcript("g", 710000), cwd=root, prompt="finish session")
        self.assertIn("FINISH SESSION", self.ctx(out))
        self.assertIn('report "%s"' % root, self.ctx(out))
        self.start("k", root)
        self.hook("UserPromptSubmit", "k", self.transcript("k", 1000), cwd=root, prompt="start long mode")
        self.hook("UserPromptSubmit", "k", self.transcript("k", 810000), cwd=root, prompt="go")
        out = self.hook("UserPromptSubmit", "k", self.transcript("k", 812000), cwd=root, prompt="finish session")
        self.assertIn("hand-off was already prepared at 800k", out.get("reason"))

    def test_break_rule_records_command_word_for_word_and_locks(self):
        root = self.migrated()
        _, sid = self.start("b", root)
        self.sg("task", "new", "--title", "A", "--approval", "ok", "--session", sid, "--root", root, ok=True)
        cmd = "Şimdi render'ı başlat, sonra 'finish' yazma!"
        out = self.hook("UserPromptSubmit", "b", self.transcript("b", 200000, minutes_ago=90), cwd=root, prompt=cmd)
        self.assertIn("BREAK RULE", self.ctx(out))
        self.assertEqual(self.store(root)["tasks"]["TASK-002"]["pending_user_command"], cmd)
        self.sg("handoff", "write", "TASK-002", "--technical", "t", "--session", sid, "--root", root, ok=True)
        self.assertEqual(self.store(root)["tasks"]["TASK-002"]["handoff"]["user_command"], cmd)
        self.assertEqual(self.hook("UserPromptSubmit", "b", self.transcript("b", 200000), cwd=root,
                                   prompt="x").get("decision"), "block")

    def test_break_small_context_reminds_and_long_mode_ignores(self):
        root = self.project()
        self.start("s", root)
        out = self.hook("UserPromptSubmit", "s", self.transcript("s", 50000, minutes_ago=90), cwd=root, prompt="go")
        self.assertIn("BREAK REMINDER", self.ctx(out))
        self.hook("UserPromptSubmit", "s", self.transcript("s", 1000), cwd=root, prompt="start long mode")
        out = self.hook("UserPromptSubmit", "s", self.transcript("s", 200000, minutes_ago=90), cwd=root, prompt="go")
        self.assertNotIn("BREAK", self.ctx(out))

    def test_start_long_mode_after_a_break_does_not_skip_the_break_rule(self):
        root = self.project()
        self.start("lb", root)
        out = self.hook("UserPromptSubmit", "lb", self.transcript("lb", 180000, minutes_ago=882), cwd=root,
                        prompt="start long mode")                                  # the v1.4 case (145k then)
        self.assertIn("BREAK RULE", self.ctx(out))
        self.assertIn("LONG MODE was NOT turned on", self.ctx(out))
        self.assertIn("Mode: normal", self.ctx(out))
        self.assertEqual(self.hook("UserPromptSubmit", "lb", self.transcript("lb", 180000), cwd=root,
                                   prompt="x").get("decision"), "block")

    def test_break_measurement_is_off_by_default_and_can_be_turned_off(self):
        self.env["CLAUDE_CODE_ENTRYPOINT"] = "claude-desktop"
        root = self.project()
        self.start("m", root)
        out = self.hook("UserPromptSubmit", "m", self.transcript("m", 120000, minutes_ago=90), cwd=root, prompt="go")
        self.assertIn("BREAK REMINDER", self.ctx(out))
        self.assertNotIn("BREAK MEASUREMENT", self.ctx(out))                         # off for everyone by default
        self.assertIn("break measurement ON", self.sg("breaks", "on", ok=True))
        out = self.hook("UserPromptSubmit", "m", self.transcript("m", 120000, minutes_ago=90), cwd=root, prompt="go")
        self.assertIn("BREAK MEASUREMENT", self.ctx(out))
        self.assertNotIn("QUOTA READING", self.ctx(out))                              # one reading request only
        usage = lambda p: {"plan": {"windows": [{"label": "5-hour limit", "percentUsed": p,
                                                 "resetsAt": stamp(-120)}]}}
        for p in (4, 6):
            self.hook("PostToolUse", "m", self.transcript("m", 120000), cwd=root,
                      tool_name="mcp__ccd_session_mgmt__get_usage", tool_response=usage(p))
        out = self.sg("breaks", "show", ok=True)
        self.assertIn("1 rows", out)
        self.assertIn("| 90 | 120k | normal | remind | n/a > 4.0% > 6.0% |", out)
        self.assertNotIn("BREAK MEASUREMENT", self.ctx(self.hook("UserPromptSubmit", "m", self.transcript("m", 120000),
                                                                 cwd=root, prompt="go")))  # no break: no measurement
        self.assertIn("break measurement OFF", self.sg("breaks", "off", ok=True))
        out = self.hook("UserPromptSubmit", "m", self.transcript("m", 120000, minutes_ago=90), cwd=root, prompt="go")
        self.assertNotIn("BREAK MEASUREMENT", self.ctx(out))                         # turned off again

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

    def test_plugin_is_quiet_in_the_cloud(self):
        # In the cloud the repository copy guards (and a computer project's own guard hook blocks the session); a
        # plugin carried into a cloud session says nothing, so other repositories get no "setup pending" noise.
        root = self.migrated()
        env = dict(self.env, CLAUDE_CODE_REMOTE="true")
        self.assertEqual(self.hook("SessionStart", "cl", cwd=root, env=env), {})
        self.assertEqual(self.hook("UserPromptSubmit", "cl", cwd=root, prompt="hi", env=env), {})

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


class Work(Base):
    """Hand-off ready work: the folder on the computer, a side branch per task in the cloud."""

    def repo(self, name, home):
        bare = os.path.join(self.home, name + ".git")
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", bare], check=True)
        root = self.project(name, git=True, files={"app.py": "print(1)\n"})
        self.git(root, "add", "-A")
        self.git(root, "commit", "-q", "-m", "app")
        self.git(root, "remote", "add", "origin", bare)
        self.sg("migrate", "discover", "--add", root, ok=True)
        self.sg("migrate", "survey", root, ok=True)
        plan = os.path.join(self.home, name + "-plan.json")
        json.dump({"name": name, "home": home, "tasks": []}, open(plan, "w"))
        self.sg("migrate", "apply", root, "--plan", plan, "--session", S3, ok=True)
        self.sg("migrate", "verify", root, ok=True)
        if home == "cloud":
            self.sg("install", "cloud", root, ok=True)
        self.git(root, "add", "-A")
        self.git(root, "commit", "-q", "-m", "session guard")
        self.git(root, "push", "-q", "origin", "main")
        return root, bare

    def cloud(self, root):
        return dict(self.env, CLAUDE_CODE_REMOTE="true", CLAUDE_PROJECT_DIR=root)

    def c(self, root, *args, sid=S1, ok=None):
        return self.sg(*args, "--session", sid, "--root", root, env=self.cloud(root), ok=ok)

    def chook(self, root, event, cid, **extra):
        data = dict({"hook_event_name": event, "session_id": cid, "transcript_path": "none.jsonl", "cwd": root,
                     "source": "startup", "prompt": ""}, **extra)
        r = subprocess.run([sys.executable, "-I", SCRIPT, "--project"], input=json.dumps(data).encode(),
                           capture_output=True, env=self.cloud(root))
        out = r.stdout.decode()
        return json.loads(out) if out else {}

    def write(self, root, rel, text):
        with open(os.path.join(root, rel), "w") as fh:
            fh.write(text)

    def test_computer_keeps_work_in_the_folder(self):
        root, bare = self.repo("pc", "computer")
        self.sg("task", "new", "--title", "A", "--approval", "ok", "--session", S1, "--root", root, ok=True)
        self.write(root, "app.py", "print(2)\n")
        out = self.sg("handoff", "write", "TASK-002", "--technical", "x", "--session", S1, "--root", root, ok=True)
        self.assertIn("work: this folder (1 uncommitted", out)
        self.assertIn("uncommitted: app.py", self.sg("task", "show", "TASK-002", "--root", root))
        self.sg("handoff", "note", "--text", "n", "--session", S1, "--root", root, ok=True)
        self.assertIn("kept in this folder", self.sg("handoff", "publish", "--session", S1, "--root", root, ok=True))
        self.assertEqual(self.git(bare, "branch", "--list", "sg/*"), "")       # no side branch, no push
        self.assertIn("app.py", self.git(root, "status", "--porcelain"))        # the work is not committed

    def test_computer_side_branch_only_when_asked(self):
        root, bare = self.repo("pc2", "computer")
        self.sg("task", "new", "--title", "A", "--approval", "ok", "--session", S1, "--root", root, ok=True)
        self.write(root, "app.py", "print(2)\n")
        self.sg("handoff", "write", "TASK-002", "--technical", "x", "--side-branch", "--session", S1, "--root", root,
                ok=True)
        self.sg("handoff", "note", "--text", "n", "--session", S1, "--root", root, ok=True)
        self.assertIn("VERIFIED", self.sg("handoff", "publish", "--session", S1, "--root", root, ok=True))
        self.assertEqual(self.git(bare, "show", "sg/task-002:app.py"), "print(2)")

    def test_cloud_side_branch_round_trip(self):
        root, bare = self.repo("cp", "cloud")
        self.c(root, "task", "new", "--title", "Feature", "--approval", "ok", ok=True)
        self.write(root, "app.py", "print(2)\n")
        self.write(root, "feature.py", "x = 1\n")
        out = self.c(root, "handoff", "write", "TASK-002", "--technical", "run app.py", "--pending", "tests", ok=True)
        self.assertIn("side branch sg/task-002", out)
        self.c(root, "handoff", "note", "--text", "n", ok=True)
        out = self.c(root, "handoff", "publish", ok=True)
        self.assertIn("TASK-002 → sg/task-002: push VERIFIED", out)
        self.assertEqual(self.git(bare, "show", "sg/task-002:feature.py"), "x = 1")
        self.assertEqual(self.git(root, "status", "--porcelain", "--", "app.py", "feature.py"), "")
        self.assertEqual(self.git(bare, "show", "main:app.py"), "print(1)")     # main untouched
        # a new cloud session starts from main and has neither the work nor the hand-off
        root2 = os.path.join(self.home, "work", "cp2")
        subprocess.run(["git", "clone", "-q", bare, root2], check=True)
        self.c(root2, "task", "new", "--title", "Other", "--approval", "ok", sid=S2, ok=True)  # also TASK-002 here
        out = self.chook(root2, "SessionStart", "c2")
        self.assertIn("HAND-OFF BRANCHES not in this checkout: sg/task-002", self.ctx(out))
        out = self.c(root2, "handoff", "adopt", "sg/task-002", sid=S2, ok=True)
        self.assertIn("Hand-offs waiting: TASK-003", out)                       # renumbered: ids never collide
        data = self.store(root2)
        self.assertEqual(data["tasks"]["TASK-002"]["title"], "Other")
        self.assertEqual(data["tasks"]["TASK-003"]["title"], "Feature")
        self.assertEqual(data["tasks"]["TASK-003"]["handoff"]["task_id"], "TASK-003")
        self.assertEqual(self.text(root2, "feature.py"), "x = 1\n")
        self.assertIn("Work: side branch sg/task-002", self.c(root2, "task", "show", "TASK-003", sid=S2))
        self.c(root2, "task", "claim", "TASK-003", sid=S2, ok=True)
        self.assertNotIn("HAND-OFF BRANCHES", self.ctx(self.chook(root2, "SessionStart", "c3")))
        self.git(root2, "push", "-q", "origin", "HEAD:main")                         # the side branch is merged
        out = self.chook(root2, "SessionStart", "c4")
        self.assertIn("safe to delete on GitHub: sg/task-002", out.get("systemMessage", ""))
        self.assertIn("Automatically delete head branches", out.get("systemMessage", ""))

    def test_cloud_several_tasks_need_paths_and_nothing_unsaved_is_published(self):
        root, _ = self.repo("cs", "cloud")
        for n in "AB":
            self.c(root, "task", "new", "--title", n, "--approval", "ok", ok=True)
        self.write(root, "a.py", "a\n")
        self.write(root, "b.py", "b\n")
        self.assertIn("--paths", self.c(root, "handoff", "write", "TASK-002", "--technical", "x", ok=False))
        self.c(root, "handoff", "write", "TASK-002", "--technical", "x", "--paths", "a.py", ok=True)
        self.c(root, "handoff", "note", "--text", "n", ok=True)
        self.assertIn("b.py", self.c(root, "handoff", "publish", ok=False))     # b.py would be lost
        self.c(root, "handoff", "write", "TASK-003", "--technical", "y", "--paths", "b.py", ok=True)
        self.assertIn("must list TASK-003", self.c(root, "handoff", "publish", ok=False))
        self.c(root, "handoff", "note", "--text", "n", ok=True)
        out = self.c(root, "handoff", "publish", ok=True)
        self.assertIn("sg/task-002: push VERIFIED", out)
        self.assertIn("sg/task-003: push VERIFIED", out)

    def test_cloud_stop_hook_keeps_unsaved_work(self):
        root, _ = self.repo("cst", "cloud")
        self.chook(root, "SessionStart", "f")
        sid = json.load(open(os.path.join(self.home, ".claude", "session_guard_v2", "sessions", "f.json")))["sg_id"]
        self.c(root, "task", "new", "--title", "A", "--approval", "ok", sid=sid, ok=True)
        self.write(root, "a.py", "a\n")
        out = self.chook(root, "UserPromptSubmit", "f", prompt="finish session")
        self.assertIn("FINISH SESSION", self.ctx(out))
        self.assertIn("side branch", self.ctx(out))
        out = self.chook(root, "Stop", "f")
        self.assertEqual(out.get("decision"), "block")
        self.assertIn("save the unfinished work (a.py)", out["reason"])

    def test_store_merge_keeps_both_tasks_and_strongest_session_state(self):
        sys.path.insert(0, os.path.join(PLUGIN, "scripts"))
        from sg2 import store as st
        base = st.blank("p", "cloud")
        ours, theirs = json.loads(json.dumps(base)), json.loads(json.dumps(base))
        st.new_task(ours, S1, "mine", approval="ok")
        st.new_task(theirs, S2, "theirs", approval="ok")
        st.new_task(theirs, S2, "theirs sub", parent="TASK-001")
        st.register_session(ours, S1, "c1", "cloud")
        st.register_session(theirs, S1, "c1", "cloud")
        st.lock_session(theirs, S1, "handed")
        m = st.merge(ours, theirs)
        self.assertEqual({t["title"] for t in m["tasks"].values()}, {"mine", "theirs", "theirs sub"})
        self.assertEqual(m["tasks"]["TASK-002-1"]["parent"], "TASK-002")
        self.assertEqual(m["next_task"], 3)
        self.assertEqual(m["sessions"][S1]["state"], "locked")


class Cleanup(Base):
    """STATUS by section, rules reviewed for scripts, old task files moved and retired, uninstall."""

    def test_status_edit_leaves_no_empty_lines(self):
        root = self.migrated()
        p = os.path.join(root, "docs", "STATUS.md")
        with open(p, "a", encoding="utf-8") as fh:
            fh.write("\n## Notes\n\n\n- kept note   \n-\n- \n\n\n- second note\n\n\n")
        out = self.hook("PostToolUse", "st", tool_name="Edit", tool_input={"file_path": p}, cwd=root)
        self.hook("SessionStart", "st", cwd=root)
        out = self.hook("PostToolUse", "st", tool_name="Edit", tool_input={"file_path": p}, cwd=root)
        text = self.text(p)
        self.assertNotIn("\n\n\n", text)
        self.assertNotRegex(text, r"(?m)^-\s*$")
        self.assertNotRegex(text.split("## Notes")[1], r"(?m)^- ")                # Notes bullets left STATUS
        ideas = self.text(root, "docs", "IDEA_TASKS.md")
        self.assertIn("### IDEA-001 · kept note", ideas)                             # ...and became ideas
        self.assertIn("### IDEA-002 · second note", ideas)
        self.assertIn("ideas 3/50: docs/IDEA_TASKS.md", text)

    def test_rule_review_on_new_claude_md_rules(self):
        root = self.migrated(files={"CLAUDE.md": "# Rules\n- Answer in Turkish, short sentences.\n"})
        out, sid = self.start("rv", root)
        self.assertNotIn("RULE REVIEW: CLAUDE.md", self.ctx(out))          # reviewed at migration
        cm = os.path.join(root, "CLAUDE.md")
        with open(cm, "a", encoding="utf-8") as fh:
            fh.write("- Every commit message starts with the TASK-ID.\n")
        out = self.hook("PostToolUse", "rv", tool_name="Edit", tool_input={"file_path": cm}, cwd=root)
        self.assertIn("RULE REVIEW: CLAUDE.md has 1 new", self.ctx(out))
        self.assertIn("Every commit message starts with the TASK-ID.", self.ctx(out))
        self.assertIn("starts with the TASK-ID", self.sg("rules", "check", "--root", root))
        self.sg("idea", "add", "Script instead of a rule: commit-msg hook", "--source", "rule-review", "--session", sid,
                "--root", root, ok=True)
        self.sg("rules", "reviewed", "--session", sid, "--root", root, ok=True)
        out, _ = self.start("rv2", root)
        self.assertNotIn("RULE REVIEW: CLAUDE.md", self.ctx(out))
        self.assertIn("commit-msg hook", self.text(root, "docs", "IDEA_TASKS.md"))

    def test_task_files_found_by_content_moved_and_retired(self):
        bare = os.path.join(self.home, "tl.git")
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", bare], check=True)
        root = self.project("tl", git=True, files={
            "misc/list.md": "Things\n- [ ] fix login page\n- [x] set up the database\n",
            "README.md": "# App\nIt runs.\n\n## To do\n- [ ] write docs\n\n## Use\nRun it.\n",
            "CLAUDE.md": "# Rules\n- Short answers.\n- Deploy the login fix before the Friday release\n",
            "docs/STATUS.md": "# S\n- Deploy the login fix before the Friday release.\n"})
        self.git(root, "add", "-A")
        self.git(root, "commit", "-q", "-m", "files")
        self.git(root, "remote", "add", "origin", bare)
        self.git(root, "push", "-q", "origin", "main")
        self.sg("migrate", "discover", "--add", root, ok=True)
        self.sg("migrate", "survey", root, ok=True)
        survey = json.load(open(glob_one(os.path.join(self.home, ".claude", "session_guard_v2", "migration",
                                                      "*.survey.json")), encoding="utf-8"))
        found = {f["path"]: f for f in survey["task_list_files"]}
        self.assertEqual((found["misc/list.md"]["open_items"], found["misc/list.md"]["done_items"]), (1, 1))
        self.assertEqual(survey["duplicates"][0]["rule_line"], "- Deploy the login fix before the Friday release")
        plan = os.path.join(self.home, "tl-plan.json")
        json.dump({"name": "tl", "tasks": [{"title": "fix login page"}], "completed": ["set up the database"],
                   "queue": [{"title": "write docs"}], "ideas": ["dark theme"], "not_carried": ["old wish"],
                   "remove_text": [{"file": "README.md", "text": "## To do\n- [ ] write docs"}],
                   "retire": ["misc/list.md"], "claude_md_remove": ["- Deploy the login fix before the Friday release"],
                   "sg_conflicts": [{"file": "CLAUDE.md", "line": "- Never write hand-off notes.",
                                     "conflicts_with": "hand off every task you own"}],
                   "rule_scripts": ["short answers: a reply-length check"]}, open(plan, "w"))
        with open(os.path.join(root, "CLAUDE.md"), "a", encoding="utf-8") as fh:
            fh.write("- Never write hand-off notes.\n")
        self.assertIn("cannot retire", self.sg("migrate", "apply", root, "--plan", self.bad_plan(), "--session", S3,
                                               ok=False))
        self.sg("migrate", "apply", root, "--plan", plan, "--session", S3, ok=True)
        self.assertFalse(os.path.exists(os.path.join(root, "misc", "list.md")))
        self.assertNotIn("Friday", self.text(root, "CLAUDE.md"))
        self.assertNotIn("Never write hand-off", self.text(root, "CLAUDE.md"))     # contradicted Session Guard
        self.assertIn("Rule removed, Session Guard's rule stays", self.text(root, "docs", "STATUS_ARCHIVE.md"))
        self.assertIn("- Short answers.", self.text(root, "CLAUDE.md"))
        self.assertIn("set up the database", self.text(root, "docs", "COMPLETED_TASKS.md"))
        self.assertIn("Script instead of a rule: short answers", self.text(root, "docs", "IDEA_TASKS.md"))
        self.assertIn("Retired (only listed tasks", self.text(root, "docs", "STATUS_ARCHIVE.md"))
        backups = os.path.join(self.home, ".claude", "session_guard_v2", "backups")
        self.assertFalse(any("list.md" in f for _, _, fs in os.walk(backups) for f in fs))   # no copy kept
        self.assertIn("PRE20-DONE-001 · set up the database", self.text(root, "docs", "COMPLETED_TASKS.md"))
        self.assertEqual(self.text(root, "README.md"), "# App\nIt runs.\n\n## Use\nRun it.\n")
        data = self.store(root)
        q = [t for t in data["tasks"].values() if t["status"] == "queued"]
        self.assertEqual((q[0]["title"], q[0]["origin"][:7]), ("write docs", "pre-2.0"))
        self.assertEqual(data["ideas"]["PRE20-IDEA-001"]["text"], "dark theme")
        self.assertIn("Not carried over (limit 50 queued tasks / 50 ideas): old wish",
                      self.text(root, "docs", "STATUS_ARCHIVE.md"))
        self.sg("migrate", "verify", root, ok=True)
        self.sg("migrate", "push", root, "--approval", "yes", ok=True)
        self.assertEqual(self.git(bare, "ls-tree", "-r", "--name-only", "main", "misc"), "")  # deletion pushed
        self.assertNotIn("To do", self.git(bare, "show", "main:README.md"))                   # the cut too

    def bad_plan(self):
        p = os.path.join(self.home, "bad-plan.json")
        json.dump({"name": "x", "tasks": [], "retire": ["../outside.md"]}, open(p, "w"))
        return p

    def test_remove_from_a_cloud_repository_and_a_computer(self):
        root = self.project("rm", git=True)
        self.sg("migrate", "discover", "--add", root, ok=True)
        self.sg("migrate", "survey", root, ok=True)
        plan = os.path.join(self.home, "rm-plan.json")
        json.dump({"name": "rm", "home": "cloud", "tasks": [{"title": "A"}]}, open(plan, "w"))
        self.sg("migrate", "apply", root, "--plan", plan, "--session", S3, ok=True)
        self.sg("install", "cloud", root, ok=True)
        out = self.sg("install", "remove-project", root, ok=True)
        self.assertIn("7 Session Guard hook(s) removed", out)
        self.assertNotIn("session-guard2", self.text(root, ".claude", "settings.json"))
        self.assertFalse(os.path.exists(os.path.join(root, ".claude", "session-guard2", "bin")))
        self.assertFalse(os.path.exists(os.path.join(root, ".claude", "skills", "session-guard")))
        status = self.text(root, "docs", "STATUS.md")
        self.assertNotIn("sg-status:begin", status)
        self.assertIn("TASK-002 · A", status)                      # the last task list stays readable
        self.assertTrue(os.path.exists(os.path.join(root, ".claude", "session-guard2", "store.json")))
        self.sg("install", "remove-project", root, "--remove-store", ok=True)
        self.assertFalse(os.path.exists(os.path.join(root, ".claude", "session-guard2")))
        out = self.sg("install", "uninstall", "--purge-state", ok=True)
        self.assertIn("removed", out)
        self.assertFalse(os.path.exists(os.path.join(self.home, ".claude", "session_guard_v2")))
        self.assertNotIn("session-guard2:begin", self.text(self.home, ".claude", "CLAUDE.md"))


class Limits(Base):
    """10 active per working agent (+3 urgent), 50 queued, 50 ideas, each in its own generated file; rules by id."""

    def setUp(self):
        super().setUp()
        self.root = self.migrated(files={"CLAUDE.md": "# Rules\n- Commit messages start with the task id.\n"})

    def s(self, *args, ok=None):
        return self.sg(*args, "--session", S1, "--root", self.root, ok=ok)

    def test_queue_and_idea_limits_duplicates_and_start(self):
        def fill(store, d):
            for n in range(49):
                store.new_task(d, S1, unique("queued work", n), queue=True, approval="ok")   # TASK-002..TASK-050
            for n in range(48):                                  # the migration brought one idea already
                store.add_idea(d, S1, unique("idea", n))
        in_store(self.root, fill)
        self.s("task", "new", "--title", "the fiftieth queued task", "--queue", "--approval", "ok", ok=True)
        self.assertIn("50 tasks already wait", self.s("task", "new", "--title", "fifty-first", "--queue",
                                                      "--approval", "ok", ok=False))
        self.s("idea", "add", "the fiftieth idea", ok=True)
        self.assertIn("50 ideas already wait", self.s("idea", "add", "one more", ok=False))
        self.s("idea", "add", "Script instead of a rule: commit-msg hook", "--source", "rule-review", ok=True)
        self.assertIn("near copy", self.s("idea", "add", "the fiftieth idea", "--source", "rule-review", ok=False))
        self.assertIn("started", self.s("task", "start", "TASK-002", ok=True))
        status = self.text(self.root, "docs", "STATUS.md")
        self.assertIn("Queued tasks 49/50: docs/TASK_BACKLOG.md · ideas 50/50: docs/IDEA_TASKS.md", status)
        backlog = self.text(self.root, "docs", "TASK_BACKLOG.md")
        self.assertIn("## Queued tasks (49/50)", backlog)
        self.assertIn("### TASK-003 · P2 · queued work 1", backlog)                 # one section per id
        self.assertNotIn("IDEA-", backlog)                                          # ideas have their own file
        ideas = self.text(self.root, "docs", "IDEA_TASKS.md")
        self.assertIn("## Ideas (50/50)", ideas)
        self.assertIn("## Rule review: scripts instead of rules (not counted)", ideas)
        self.assertNotIn("### TASK-", ideas)
        out = self.sg("task", "check", "--title", "queued work 4", "--root", self.root)
        self.assertIn("queued 49/50 · ideas 50/50", out)
        self.assertIn("- TASK-006 · P2 · queued work 4", out)
        bl = os.path.join(self.root, "docs", "TASK_BACKLOG.md")
        with open(bl, "a", encoding="utf-8") as fh:
            fh.write("- sneaked in by hand\n")
        self.hook("SessionStart", "lim", cwd=self.root)
        out = self.hook("PostToolUse", "lim", tool_name="Edit", tool_input={"file_path": bl}, cwd=self.root)
        self.assertIn("hand edit was undone", self.ctx(out))
        self.assertNotIn("sneaked", self.text(bl))
        il = os.path.join(self.root, "docs", "IDEA_TASKS.md")
        with open(il, "a", encoding="utf-8") as fh:
            fh.write("- a fresh thought about logging\n")
        out = self.hook("PostToolUse", "lim", tool_name="Edit", tool_input={"file_path": il}, cwd=self.root)
        self.assertIn("50 ideas already wait", self.ctx(out))                        # limit holds for hand edits
        self.assertIn("- a fresh thought about logging", self.text(il))             # the refused bullet stays

    def test_rule_ids_and_rule_script_task(self):
        cm = os.path.join(self.root, "CLAUDE.md")
        self.assertIn("<!-- R-001 -->", self.text(cm))           # ids given at migration
        with open(cm, "a", encoding="utf-8") as fh:
            fh.write("- Never push to main without a review.\n\n| tool | use |\n|---|---|\n| git | commits |\n\n"
                     "<!-- a note\nover two lines -->\n\n    indented code line\n")
        self.assertIn("Never push", self.sg("rules", "check", "--root", self.root))
        self.s("rules", "reviewed", ok=True)
        self.assertIn("review. <!-- R-002 -->", self.text(cm))
        for kept in ("| git | commits |\n", "over two lines -->\n", "    indented code line\n"):
            self.assertIn(kept, self.text(cm))                  # no id written into tables, comments or code
        with open(cm, "a", encoding="utf-8") as fh:
            fh.write("- Skip hand-offs when the session ends.\n")
        self.hook("SessionStart", "rr", cwd=self.root)
        out = self.hook("PostToolUse", "rr", tool_name="Edit", tool_input={"file_path": cm}, cwd=self.root)
        self.assertIn("SESSION GUARD FIRST", self.ctx(out))
        self.s("rules", "reviewed", ok=True)
        rid = re.search(r"Skip hand-offs when the session ends\. <!-- (R-\d+) -->", self.text(cm)).group(1)
        self.assertIn("--conflict", self.s("rules", "remove", rid, ok=False))          # neither yes nor conflict
        self.assertIn("contradicts Session Guard", self.s("rules", "remove", rid, "--conflict",
                                                          "hand off every task you own", ok=True))
        self.assertNotIn("Skip hand-offs", self.text(cm))
        self.assertIn("Rule %s removed" % rid, self.text(self.root, "docs", "STATUS_ARCHIVE.md"))
        self.s("idea", "add", "Script instead of a rule: commit-msg hook checks the task id", "--source",
               "rule-review", "--rule", "R-001", ok=True)
        out = self.s("idea", "promote", "IDEA-001", "--approval", "ok", ok=True)
        tid = out.split("→ ")[1].split()[0]
        self.assertEqual(self.store(self.root)["tasks"][tid]["kind"], "rule-script")
        self.assertIn("Commit messages", self.text(cm))         # the rule stays until the script is verified
        self.assertIn("--verified", self.s("task", "done", tid, ok=False))
        self.s("task", "done", tid, "--verified", "hook rejected a bad message, accepted a good one", ok=True)
        self.assertNotIn("Commit messages", self.text(cm))
        self.assertIn("R-002", self.text(cm))
        self.assertEqual(self.store(self.root)["rules"]["R-001"]["state"], "replaced by script")


def glob_one(pattern):
    import glob
    found = glob.glob(pattern)
    assert len(found) == 1, found
    return found[0]


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
        json.dump({"tasks": [{"title": "t%d" % n} for n in range(11)]}, open(plan, "w"))
        self.assertIn("at most 10", self.sg("migrate", "apply", root, "--plan", plan, "--session", S3,
                                                          ok=False))
        self.sg("migrate", "survey", root, ok=True)
        self.assertIn("at most 10", self.sg("migrate", "apply", root, "--plan", plan, "--session", S3, ok=False))
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


class Places(Base):
    def settings(self):
        p = os.path.join(self.home, ".claude", "settings.json")
        return json.load(open(p)) if os.path.exists(p) else {}

    def put_settings(self, data):
        json.dump(data, open(os.path.join(self.home, ".claude", "settings.json"), "w"))

    def test_status_line_installed_v1_repointed_other_left_alone(self):
        self.assertIn("session-guard2-statusline", self.settings()["statusLine"]["command"])
        self.put_settings({"statusLine": {"type": "command", "command": "python old.py # session-guard-statusline"}})
        self.assertIn("re-pointed from 1.x", self.sg("install", "approve", "--executive", self.exe))
        self.put_settings({"statusLine": {"type": "command", "command": "ccstatus"}})
        self.assertIn("another tool", self.sg("install", "approve", "--executive", self.exe))
        self.assertEqual(self.settings()["statusLine"]["command"], "ccstatus")

    def test_status_line_saves_quota_readings(self):
        data = {"rate_limits": {"five_hour": {"used_percentage": 42, "resets_at": time.time() + 3600},
                                "seven_day": {"used_percentage": 7, "resets_at": time.time() + 86400}}}
        r = subprocess.run([sys.executable, "-I", SCRIPT, "statusline"], input=json.dumps(data).encode(),
                           capture_output=True, env=self.env)
        self.assertIn("5-hour 42% · weekly 7%", r.stdout.decode("utf-8"))

    def test_git_migration_adds_computer_guard_that_blocks_cloud(self):
        root = self.project("cg", git=True, files={".claude/settings.json": json.dumps({"hooks": {"UserPromptSubmit": [
            {"hooks": [{"type": "command", "command": "exit 0 # session-guard-home"}]}]}, "model": "x"})})
        self.sg("migrate", "discover", "--add", root, ok=True)
        self.sg("migrate", "survey", root, ok=True)
        plan = os.path.join(self.home, "p.json")
        json.dump({"tasks": []}, open(plan, "w"))
        self.assertIn("computer guard added", self.sg("migrate", "apply", root, "--plan", plan, "--session", S3, ok=True))
        st = json.load(open(os.path.join(root, ".claude", "settings.json")))
        cmds = [h["command"] for g in st["hooks"]["UserPromptSubmit"] for h in g["hooks"]]
        self.assertEqual(len(cmds), 1)                       # the 1.x guard was replaced, not doubled
        self.assertIn("session-guard2-home", cmds[0])
        self.assertEqual(st["model"], "x")                   # the user's settings are kept
        r = subprocess.run(["bash", "-c", cmds[0]], capture_output=True, env=dict(self.env, CLAUDE_CODE_REMOTE="true"))
        self.assertEqual(json.loads(r.stdout.decode())["decision"], "block")
        r = subprocess.run(["bash", "-c", cmds[0]], capture_output=True, env=self.env)
        self.assertEqual(r.stdout, b"")

    def test_cloud_copy_idempotent_replaces_v1_and_quiet_beside_plugin(self):
        root = self.project("cloudp", git=True, files={".claude/settings.json": json.dumps({"hooks": {"SessionStart": [
            {"hooks": [{"type": "command", "command": "python .claude/session-guard/session_guard.py --project"}]}]}})})
        self.assertIn("hooks added: 7", self.sg("install", "cloud", root))
        self.assertIn("hooks added: 0", self.sg("install", "cloud", root))
        st = json.load(open(os.path.join(root, ".claude", "settings.json")))
        self.assertNotIn(".claude/session-guard/session_guard.py", json.dumps(st))
        copy = os.path.join(root, ".claude", "session-guard2", "bin", "scripts", "session_guard.py")
        env = dict(self.env, CLAUDE_CODE_REMOTE="true", CLAUDE_PROJECT_DIR=root)
        run = lambda event, cid, **kw: subprocess.run(
            [sys.executable, "-I", copy, "--project"], capture_output=True, env=env, input=json.dumps(dict(
                {"hook_event_name": event, "session_id": cid, "transcript_path": "x", "cwd": root,
                 "source": "startup", "prompt": "hi"}, **kw)).encode()).stdout.decode()
        self.assertIn("Session Guard 2.0", run("SessionStart", "c1"))
        self.assertIn("MIGRATION PENDING", run("SessionStart", "c1"))
        fake = os.path.join(self.home, ".claude", "plugins", "cache", "sg", "scripts")
        os.makedirs(fake)
        open(os.path.join(fake, "session_guard.py"), "w").close()
        self.assertIn("Session Guard 2.0", run("SessionStart", "c2"))  # cloud: the copy guards, plugin or not
        env.pop("CLAUDE_CODE_REMOTE")
        self.assertEqual(run("SessionStart", "c3"), "")       # computer: the plugin guards, the copy is quiet

    def test_computer_project_gets_no_cloud_copy(self):
        root = self.project("cp", git=True, files={".claude/settings.json": json.dumps({"hooks": {"UserPromptSubmit": [
            {"hooks": [{"type": "command", "command": "x # session-guard2-home"}]}]}})})
        self.assertIn("computer project", self.sg("install", "cloud", root))


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
