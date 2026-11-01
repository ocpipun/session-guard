"""Session Guard: Claude Code hook script (SessionStart, UserPromptSubmit, PostToolUse) and status line.

Claude cannot see its own context size or how long the user was away. This
script measures both from the session transcript and tells Claude
(additionalContext) and the user (systemMessage) what to do at each threshold.

- Context: input tokens of the last main-agent reply in the transcript
  (input + cache_creation + cache_read). A measurement, not an estimate.
- Gap: time since the last main-agent reply.
- State: ~/.claude/session_guard/<session_id>.json (long mode, lock, notified
  thresholds, recent message ids). When the app reopens a conversation under a
  new session id, the state is found again through the shared message ids.
- A locked session never passes a prompt to Claude (decision: block), so it
  costs nothing. Lock types: context, finish, break (permanent), quota (until
  the reset), restart (a session started before this install or update).
- Session Follow: the 5-hour and weekly quota, shared by all local sessions in
  ~/.claude/session_guard/session_follow/. Read from the terminal status line
  (zero tokens) or, in the desktop app, from the usage tool Claude is asked to
  call. Past 90% / 98% with the reset 45+ minutes away the session stops.
- Setup gate: Session Guard stays inactive until the user approved removing the
  rules, skills, scripts and hooks that conflict with it (skill setup). The
  approval is kept in ~/.claude/session_guard/config/setup.json (plugin) or in
  <repo>/.claude/session-guard/setup.json (project copy, cloud). A project is
  reviewed once; a project with nothing to review is approved on its own.
- Project files: in an active project the missing CLAUDE.md, docs/STATUS.md and
  docs/STATUS_ARCHIVE.md are created at session start. Existing files (any
  letter case, or .claude/CLAUDE.md) are never touched.
- General rules: kept in a marked block of ~/.claude/CLAUDE.md (plugin); the
  project copy has no global file, so it gives them at session start.

Other modes:
    session_guard.py --report [project_dir]   one-line usage summary of the
                                              latest session of a project
    session_guard.py --scan [project_dir]     what a conflict review must read
                                              (global + every known project)
    session_guard.py --install-global         writes the general rules block
    session_guard.py --setup approved|declined [--all | project_dir...]
    session_guard.py --print-rules            the rules a review compares with
    session_guard.py --create-files [project_dir]  the missing project files, now
    session_guard.py --install-statusline     signed terminal status line (quota
    session_guard.py --uninstall-statusline   readings); another tool's one is left alone
    session_guard.py --statusline             the status line command itself
    session_guard.py --set-language [code]    notice language (no code: the app's, read once)
    session_guard.py --print-messages         the user notices, to translate
    session_guard.py --set-messages file.json stores their translation
    session_guard.py --project                project-copy mode (cloud); does
                                              nothing when the plugin is installed
On any error the hook exits 0 silently: it must never break the user's work.
"""
import glob
import json
import os
import re
import shutil
import sys
import time
from datetime import datetime

K = 1000
NORMAL_LIMIT, LONG_LIMIT, BREAK_CONTEXT, BREAK_SECONDS = 400 * K, 900 * K, 100 * K, 3600
HOME = os.path.expanduser("~")
CLAUDE_DIR = os.path.join(HOME, ".claude")
GLOBAL_MD = os.path.join(CLAUDE_DIR, "CLAUDE.md")
STATE_DIR = os.path.join(CLAUDE_DIR, "session_guard")
CONFIG = os.path.join(STATE_DIR, "config", "setup.json")   # in a subfolder: the 30-day state cleanup never sees it
TAIL_BYTES = 4 * 1024 * 1024    # only the last 4 MB of a transcript is read
PENDING_MAX = 24 * 3600         # a background job with no finish notice counts as running this long
STATE_MAX_AGE = 30 * 86400
KEEP_IDS = 10                   # recent assistant message ids kept to recognise a reopened conversation
RULES_VERSION = 3               # raise when a rule change needs a new conflict review (1 = v1.0.0, 2 = v1.1)
SELF = os.path.abspath(__file__)
PROJECT_MODE = False            # set by --project (copy inside a repository, for cloud sessions)

# Commands count only at the start of the message (followed by punctuation or
# the end) or at its end; inside a sentence or a quote they are ignored.
FINISH = r"finish\s+session"
LONG = r"start\s+long\s+mode"

# (threshold, key, instruction for Claude, message for the user, hard stop)
NORMAL_STEPS = [
    (400 * K, "n400", "HARD LIMIT (400k). Do no work. Tell the user the session is closed and to open a new one.",
     "Context 400k: hard limit. Session closed, open a new session.", True),
    (350 * K, "n350", "TARGET STOP (350k). Start no new task. Finish the hand-off if not done (skill session-guard). Tell the user to open a new session.",
     "Context 350k: target stop. Opening a new session is recommended.", False),
    (300 * K, "n300", "HAND-OFF (300k). Prepare the hand-off now with skill session-guard. From now on start no new task; only answer information questions and update the hand-off note on request.",
     "Context 300k: preparing hand-off. No new tasks from now on.", False),
    (250 * K, "n250", "NOTICE (250k). Tell the user the context passed 250k.",
     "Context passed 250k.", False),
]
LONG_STEPS = [
    (900 * K, "l900", "HARD LIMIT (900k, long mode). Do no work. Tell the user the session is closed.",
     "Context 900k: long-mode hard limit. Session closed, open a new session.", True),
    (800 * K, "l800", "NO WORK (800k, long mode). Do no new work. Only answer simple, specific information questions. Prepare the final hand-off note (skill session-guard) and finish it before 900k.",
     "Context 800k: work stopped. Information questions only; hand-off will be finished before 900k.", False),
    (700 * K, "l700", "NOTICE + PREPARE (700k, long mode). Tell the user. Make current work hand-off ready (hand-off note, skill session-guard). You may keep working and take new tasks.",
     "Context 700k: making work hand-off ready.", False),
    (300 * K, "l300", "NOTICE (300k, long mode). Only tell the user; no hand-off.",
     "Context passed 300k (long mode: no hand-off).", False),
    (250 * K, "l250", "NOTICE (250k, long mode). Only tell the user; no hand-off.",
     "Context passed 250k (long mode).", False),
]
# Pure notices are given once; the restrictions from 300k on repeat on every prompt.
# Every threshold reaches the user in one fixed form, so it is clearly Session Guard's and not Claude's own idea.
NOTICE_FORM = (' Tell the user in one line, in their language, in this form: "%dk threshold passed: <what this means '
               'now, in a few words>. *This is a Session Guard notice." Then keep to the instruction; in the middle of '
               'work, give the line and go on working.')
NOTIFY_ONCE = {"n250", "l250", "l300", "l700"}

# Something active for over 24 hours (PENDING_MAX) is probably a bug: the user is told, not left waiting.
STALE_JOBS = ("WARNING (24 hours): %d background job(s) or subagent(s) started over 24 hours ago never reported back. "
              "First tell the user in one line: a bug may have occurred, something has been active for over 24 hours, "
              "please look into it. Check the job if you can. If you think it is stuck, suggest ending the session "
              "with the command \"finish session\".")
LONG_TURN = ("WARNING (24 hours): you have been working on the same user message for over 24 hours. Tell the user: a "
             "bug may have occurred, the agent has been active for 24 hours, please look into it. If you think there "
             "is a problem, suggest ending the session with the command \"finish session\".")
WATCH_WAKE = ("[Session Guard] 24-HOUR WATCH: 24 hours have passed since the user's last message and the session is "
              "still busy (work or a background job has not finished). Tell the user: a bug may have occurred, the "
              "agent has been active for 24 hours, please look into it. If you think there is a problem, suggest "
              "ending the session with the command \"finish session\".")
WATCH_SECONDS = int(os.environ.get("SESSION_GUARD_WATCH_SECONDS") or 24 * 3600)  # tests shorten it
WATCH_STEP = 60                 # a watch checks once a minute whether a newer prompt replaced it (local file read, no Claude request)

RULES = """[Session Guard] Rules for this session (plugin session-guard).
- You cannot measure context size or time between messages. Session Guard measures them itself and writes to
  you ("[Session Guard] Context: ...") only when it has an instruction. Follow it. Quote its numbers; never
  invent one. Never add context size or mode to your replies on your own.
- Each threshold instruction gives the user one line: "<N>k threshold passed: <what now>. *This is a Session
  Guard notice." (in the user's language); during work give it and keep working.
- Act on a threshold only when Session Guard's instruction for it arrives, never earlier on your own: no early
  notice, no hand-off note, no "open a new session" for size or cost, even when you expect the next task to cross
  250k/300k at once. Session Guard checks after every tool call while you work and stops at 400k (900k long
  mode), so work on and follow its instruction when it comes. 250k (and 300k in long mode) is a notice only.
  This is a strict rule. Breaking it causes real problems (wasted quota, time and work, a broken hand-off plan),
  and the plugin's developer is held responsible for them. Follow this rule: never act on a threshold early.
- The "Hand-off note" section of STATUS.md is written only on a Session Guard instruction (hand-off, break,
  finish session) or when the user asks. Updating the rest of STATUS when a step ends is right.
- Session start: docs/STATUS.md (if any) is given below; do not read it again. Read docs/STATUS_ARCHIVE.md only
  when needed, and then only the matching part via search.
- Normal mode: 250k notice; 300k hand-off, then no new tasks (only information questions and hand-off note
  updates on request); 350k target stop; 400k hard stop.
- Long mode ("start long mode"; this session only, cannot be switched off): 250k/300k notice only; 700k notice and
  make work hand-off ready; 800k no work, only simple specific questions, final hand-off note; 900k hard stop.
  The break rule does not apply.
- Break (normal mode): gap > 1 hour and context > 100k -> do NOT run the user's command and do no reading or
  research for it; do the hand-off and copy the command into the note. Context <= 100k -> remind, then continue.
  Waiting for your own background job is not a break.
- "finish session" (any mode): only hand-off and session measurement, nothing else.
- Commands ("start long mode", "finish session") count only at the end of the message, or at its start followed
  by a comma or period.
- After a hard stop, a break hand-off, "finish session" or a quota stop Session Guard locks the session: prompts
  never reach you.
- Quota (computer only): when Session Guard asks for a quota reading, call the usage tool it names silently, in
  parallel with your next tool call; Session Guard reads the result itself.
- Procedures: skill session-guard.
- Reply style (saves tokens): plain words, short sentences, no needless detail; the user asks when they want more.
  Give each issue its own number. Under it, in this order and only those needed, the labels PROBLEM /
  RECOMMENDATION / ASSUMPTION / WARNING / UNVERIFIED, translated into the user's language.
- Facts: never present as fact anything you have not researched or checked, or are not sure of; label it
  UNVERIFIED. Label your own recommendations and assumptions RECOMMENDATION / ASSUMPTION, so the user knows
  they are yours and not established facts.
- Always answer in the user's language."""

# Kept between these markers in ~/.claude/CLAUDE.md; the project copy gives it at session start instead.
BLOCK_BEGIN = "<!-- session-guard:begin (managed by the Session Guard plugin; changes inside this block are overwritten) -->"
BLOCK_END = "<!-- session-guard:end -->"
GENERAL_RULES = """# Session Guard general rules
- These rules and the "[Session Guard] Rules" given at session start win over any other CLAUDE.md rule on the
  same topic, project files included. On other topics a project CLAUDE.md wins over the global one.

## Project files
- Every project has `CLAUDE.md` (permanent facts and decisions), `docs/STATUS.md` (current state) and
  `docs/STATUS_ARCHIVE.md` (history). Session Guard creates the missing ones; it never overwrites existing ones.
- STATUS.md fits on one screen and is not a log: update the existing sections, merge repeats, add no dated lines.
  Unless the project CLAUDE.md sets its own content: current goal, open issues, next step, running jobs and how
  to check them, and a "Hand-off note" section (procedure: skill session-guard).
- A line leaving STATUS that will matter later (decision, measurement, proven cause, mistake not to repeat, how to
  undo a change) goes as one dated line to STATUS_ARCHIVE.md under the matching heading; otherwise it is deleted.
  The archive is not read every session; find the heading by search. No second archive, no separate hand-off file.
- Update STATUS when a step ends, before long work, on an error, while waiting for the user, before a break.

## Topics
- Suggest a new conversation when the topic changes, whatever the token count. Do not add unrelated small work
  to a grown conversation.

## Tools and permissions
- Ask for screen, app and similar permissions right before the action that needs them, not at session start.
  Before long unattended work, ask before the user leaves.
- Load deferred tools one by one as needed (ToolSearch with `select:`), never in bulk.

## Session measurement
- At session start and on "finish session", read the weekly and 5-hour quota percentages if the app has a usage
  tool. On "finish session" also run the `--report` command Session Guard gives and add one row to the "Session
  measurements" table of STATUS_ARCHIVE.md. A value you cannot read is "n/a"; never invent a number.
- The quota formula is unknown: never present a token drop as a quota saving; compare measured percentages."""

PROJECT_TEMPLATE = """# %s

Permanent project facts and decisions. General rules: Session Guard.

## Project
- Goal:

## Decisions

## Working style
"""
STATUS_TEMPLATE = """# STATUS

## Current goal
-

## Open issues
- None

## Next step
-

## Hand-off note
- None yet
"""
ARCHIVE_TEMPLATE = """# STATUS archive

One dated line per item. Not read every session: find the heading by search.

## Decisions and measurements

## Session measurements
| Date | Requests | Cache read | Cache write | Output | Largest context | Weekly % | 5-hour % |
|---|---|---|---|---|---|---|---|
"""

# Setup and review start by themselves: the instruction comes at session start and with every prompt until answered.
PENDING_TAIL = ("Until the user approves, Session Guard measures nothing, enforces no threshold and locks nothing; do not "
                "apply its rules. Before anything else, run it now: scan, then show the one list and ask its single "
                "approval question. If you are already in the middle of it in this conversation, just continue it. If "
                "the user says it is running in another session, leave it. Handle the user's message only after the "
                "answer. Script command: %s")
SETUP_NEEDED = ("[Session Guard] SETUP PENDING: Session Guard is installed, but its one-time setup is not done (or its "
                "rules changed since then). Skill %s, \"Full setup\". " + PENDING_TAIL)
REVIEW_NEEDED = ("[Session Guard] PROJECT REVIEW PENDING (%s): this project's CLAUDE.md, similar files, skills, commands "
                 "or hooks were not yet checked against Session Guard. Skill %s, \"Project review\". " + PENDING_TAIL)
DECLINED = ("[Session Guard] OFF%s: the user kept rules that conflict with it. Do not apply Session Guard rules. "
            "If the user asks, the review can be run again (skill %s).")


# --- notices the user sees directly (shown by the app; Claude never sees them, so it cannot translate them) ---
# Language: chosen once, at setup (--set-language reads the Claude desktop app's language setting one time) or
# when the user asks for another one; it is never checked again. Terminal and cloud: English. Translations: the
# user's own (~/.claude/session_guard/config/messages/<locale>.json, written by --set-messages) or the plugin's
# (messages/<locale>.json). A language with no translation: English. Translating is the user's business (MIT).
# Placeholders: %(name)s / %(name)d.
MESSAGES = {
    "n400": "Context 400k: hard limit. Session closed, open a new session.",
    "n350": "Context 350k: target stop. Opening a new session is recommended.",
    "n300": "Context 300k: preparing hand-off. No new tasks from now on.",
    "n250": "Context passed 250k.",
    "l900": "Context 900k: long-mode hard limit. Session closed, open a new session.",
    "l800": "Context 800k: work stopped. Information questions only; hand-off will be finished before 900k.",
    "l700": "Context 700k: making work hand-off ready.",
    "l300": "Context passed 300k (long mode: no hand-off).",
    "l250": "Context passed 250k (long mode).",
    "hard_limit": "Context %(k)s, hard limit. Session closed, open a new session.",
    "closed_context": "Session closed (context limit). Open a new session.",
    "closed_finish": "Session closed (finish session). Open a new session.",
    "closed_break": "Session closed (break hand-off). Open a new session.",
    "finishing": "Finishing session: preparing hand-off. Later messages will not be delivered.",
    "break_lock": "Break over 1 hour and context %(k)s: command not run, it goes into the hand-off note.",
    "break_remind": "Break over 1 hour; context %(k)s, continuing in this session.",
    "stale": "Possible bug: something has been active for over 24 hours. Please look into it; if it is stuck, end the session with \"finish session\".",
    "restart_plugin": "This session started before Session Guard was installed or updated, so it is not protected. Your message was not sent. Fully quit Claude (tray included) and reopen it, then continue here.",
    "restart_project": "This session started before Session Guard was installed in this repository, so it is not protected. Your message was not sent. Open a new session of this repository and continue there.",
    "window_five_hour": "5-hour",
    "window_weekly": "weekly",
    "quota_block": "Quota %(percent)d%% (%(window)s), resets at %(time)s. Your message was not sent. Continue after that.",
    "quota_stopped": "Quota %(percent)d%% (%(window)s), resets at %(time)s. Work stopped. Continue after that.",
    "quota_stop": "Quota %(percent)d%% (%(window)s): finishing the current step, then locked until %(time)s.",
    "setup_pending": "Setup pending: Claude starts it with your next message.",
    "review_pending": "Project review pending: Claude starts it with your next message.",
    "declined": "Off (conflicting rules were kept).",
}
PLACEHOLDER = re.compile(r"%\((\w+)\)[sd]")
OWN_MESSAGES = os.path.join(STATE_DIR, "config", "messages")
PLUGIN_MESSAGES = os.path.join(os.path.dirname(os.path.dirname(SELF)), "messages")
_translated = None


def app_locale():
    """The language selected in the Claude desktop app ("tr", "pt-br"), read once by --set-language at setup;
    None (English) when it is English or the app's settings file is not found."""
    places = []
    for base in (os.environ.get("LOCALAPPDATA"), os.environ.get("APPDATA"),
                 os.path.join(HOME, "Library", "Application Support")):
        if base:
            places += glob.glob(os.path.join(base, "Claude-Data", "*", "config.json"))
            places.append(os.path.join(base, "Claude", "config.json"))
    for path in sorted((p for p in places if os.path.isfile(p)), key=os.path.getmtime, reverse=True):
        try:
            with open(path, encoding="utf-8-sig") as fh:
                found = json.load(fh).get("locale")
        except (OSError, ValueError, AttributeError):
            continue
        if isinstance(found, str) and re.match(r"^[A-Za-z]{2,3}([-_][A-Za-z0-9]{2,8})*$", found):
            found = found.lower().replace("_", "-")
            return None if found == "en" or found.startswith("en-") else found
    return None


def notice_locale():
    """The language chosen at setup, for the desktop app only; None (English) in the terminal and the cloud."""
    if PROJECT_MODE or os.environ.get("CLAUDE_CODE_ENTRYPOINT") != "claude-desktop":
        return None
    return load_config().get("locale") or None


def set_language(args):
    """--set-language [code|english]: no code = the desktop app's current setting, read this one time."""
    locale = (args[0].lower().replace("_", "-") if args else app_locale()) or "english"
    cfg = load_config()
    if locale in ("en", "english") or locale.startswith("en-"):
        cfg.pop("locale", None)
        save_config(cfg)
        out("notice language: English")
        return 0
    cfg["locale"] = locale
    save_config(cfg)
    global _translated
    _translated = None
    full = {n: t for n, t in load_translation(translation_file(locale, own=False)).items() if valid_translation(n, t)}
    full.update({n: t for n, t in load_translation(translation_file(locale)).items() if valid_translation(n, t)})
    left = [n for n in MESSAGES if n not in full]
    out("notice language: %s; %s" % (locale, "translation complete" if not left else
        "notices without a translation (English until translated): " + ", ".join(left)))
    return 0


def translation_file(locale, own=True):
    return os.path.join(OWN_MESSAGES if own else PLUGIN_MESSAGES, locale + ".json")


def load_translation(path):
    try:
        with open(path, encoding="utf-8-sig") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def translations():
    """The notices for the app's language: the user's own translation over the plugin's; {} for English."""
    global _translated
    if _translated is None:
        locale = load_config().get("locale")
        _translated = {}
        if locale:
            _translated.update(load_translation(translation_file(locale, own=False)))
            _translated.update({n: t for n, t in load_translation(translation_file(locale)).items() if valid_translation(n, t)})
    return _translated


def valid_translation(name, text):
    return (isinstance(text, str) and bool(text.strip()) and name in MESSAGES
            and sorted(PLACEHOLDER.findall(text)) == sorted(PLACEHOLDER.findall(MESSAGES[name])))


def msg(name, **values):
    """A user notice in the language chosen at setup when a translation exists, else in English."""
    text = translations().get(name) if notice_locale() else None
    if valid_translation(name, text):
        try:
            return "Session Guard: " + text % values
        except (KeyError, TypeError, ValueError):
            pass
    return "Session Guard: " + MESSAGES[name] % values


def missing_translations():
    """(locale, notices without a translation) for the language chosen at setup; (None, []) for English."""
    locale = load_config().get("locale")
    if not locale:
        return None, []
    found = translations()
    return locale, [n for n in MESSAGES if not valid_translation(n, found.get(n))]


def set_messages(path):
    """Stores a translation for the chosen language in the user's own file (kept over plugin updates)."""
    locale = load_config().get("locale")
    if not locale:
        out("not stored: the notice language is English (choose one first with --set-language)")
        return 1
    data = load_translation(path)
    stored = {n: t for n, t in load_translation(translation_file(locale)).items() if valid_translation(n, t)}
    bad = []
    for name in MESSAGES:
        if name in data:
            if valid_translation(name, data[name]):
                stored[name] = data[name]
            else:
                bad.append(name)
    os.makedirs(OWN_MESSAGES, exist_ok=True)
    with open(translation_file(locale), "w", encoding="utf-8") as fh:
        json.dump(stored, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    global _translated
    _translated = None
    _, left = missing_translations()
    out("stored %d notices for locale %s (%s)%s%s" % (len(stored), locale, translation_file(locale),
        "; rejected (placeholders changed): " + ", ".join(bad) if bad else "",
        "; still English: " + ", ".join(left) if left else ""))
    return 0

# --- locks -----------------------------------------------------------------
# Each type is kept on its own in state["locks"]; clearing one never clears another. "context", "finish" and
# "break" are permanent. "quota" clears when its window resets (or a lower reading arrives). "restart" is not
# stored: it holds while the session has no start marker of this script (see started()).
# --- Session Follow (quota) ---------------------------------------------------
# One file per window, shared by every local session (the quota is per account). After its reset a file is
# emptied and later overwritten, never deleted. Not offered in the cloud: no hook can read the quota there.
FOLLOW_DIR = os.path.join(STATE_DIR, "session_follow")
WINDOWS = (("five_hour", "5-hour"), ("weekly", "weekly"))
ASK_AT = {"five_hour": 85, "weekly": 95}      # from here on: a reading with every prompt
STOP_AT = {"five_hour": 90, "weekly": 98}     # QUOTA STOP, then the quota lock
READING_MAX_AGE = 20 * 60
ASK_STEP = 5 * 60                             # during work: at most one reading request per 5 minutes
RESET_MARGIN = 45 * 60                        # a reset closer than this: no stop, work goes on
QUOTA_GRACE = 15 * 60                         # time to reach a hand-over-ready state before the turn is stopped
USAGE_TOOL = "mcp__ccd_session_mgmt__get_usage"
READ_USAGE = ("[Session Guard] QUOTA READING: call the tool %s once now (no arguments), in parallel with your next "
              "tool call; if it is not loaded, load it with ToolSearch \"select:%s\". Session Guard reads the result "
              "itself; do not mention it to the user." % (USAGE_TOOL, USAGE_TOOL))
QUOTA_STOP = ("QUOTA STOP (%s quota %d%%, resets at %s). Start no new task, the user's message included if it is one. "
              "Bring the current work to a hand-over-ready state within 15 minutes. Write only one line into "
              "docs/STATUS.md: \"Stopped at: <which operation>, <in which state>\"; no hand-off note. Tell the user in "
              "one line. After 15 minutes the turn is stopped; prompts are blocked until the reset.")

# --- status line (terminal) -----------------------------------------------------
SETTINGS = os.path.join(CLAUDE_DIR, "settings.json")
STATUSLINE_SIGN = "session-guard-statusline"  # a status line command with this mark is Session Guard's own


def normalize(text):
    # Commands are English; "FINISH" on a Turkish keyboard lowercases to "fınısh", so ı/İ count as i.
    return text.replace("İ", "i").lower().replace("ı", "i")


def has_command(pattern, text):
    return bool(re.search(r"^[\W_]*" + pattern + r"(?:[.,!;:\n]|\s*$)", text) or re.search(pattern + r"[\W_]*$", text))


def to_time(stamp):
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()
    except (AttributeError, ValueError):
        return None


def is_prompt(entry, content):
    """A message the user typed (not a tool result, task notice, subagent message or app reminder)."""
    if entry.get("type") != "user" or entry.get("isMeta"):
        return False
    raw = entry["message"].get("content")
    if isinstance(raw, str):
        return not raw.lstrip().startswith("<")
    return any(isinstance(p, dict) and p.get("type") == "text" and not (p.get("text") or "").lstrip().startswith("<")
               for p in content) and not any(isinstance(p, dict) and p.get("type") == "tool_result" for p in content)


def read_transcript(path):
    """Returns (context, last_reply_time, assistant_ids, pending_jobs, stale_jobs, last_prompt_time, turn_open)
    from the transcript tail. Stale: a background job or subagent with no finish notice after 24 hours.
    turn_open: the last entry is not Claude's final text reply (a tool runs, or the prompt got no answer)."""
    if not path or not os.path.exists(path):
        return None, None, [], 0, 0, None, False
    with open(path, "rb") as fh:
        fh.seek(max(0, os.path.getsize(path) - TAIL_BYTES))
        lines = fh.read().decode("utf-8", errors="replace").splitlines()
    context = last = prompt_at = last_kind = None
    ids, launched, agent_of, done = [], {}, {}, set()
    for line in lines:
        # Finish notices: background commands (<task-notification>) and subagents (<agent-message>).
        if "<task-notification>" in line or "agent-message" in line:
            done.update(re.findall(r"<tool-use-id>(\w+)</tool-use-id>", line))
            done.update(re.findall(r"<task-id>(\w+)</task-id>", line))
            done.update(re.findall(r'agent-message from=\\*"(\w+)', line))
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        msg = entry.get("message")
        if not isinstance(msg, dict) or entry.get("isSidechain"):
            continue
        content = msg.get("content") if isinstance(msg.get("content"), list) else []
        if entry.get("type") == "assistant":
            last_kind = "tool" if any(isinstance(p, dict) and p.get("type") == "tool_use" for p in content) else "text"
            if msg.get("id") and msg.get("id") not in ids:
                ids.append(msg["id"])
            if msg.get("usage"):
                u = msg["usage"]
                context = (u.get("input_tokens") or 0) + (u.get("cache_creation_input_tokens") or 0) + (u.get("cache_read_input_tokens") or 0)
                last = to_time(entry.get("timestamp")) or last
            for part in content:
                if isinstance(part, dict) and part.get("type") == "tool_use":
                    args = part.get("input") or {}
                    if args.get("run_in_background") is True or part.get("name") == "Agent":
                        launched[part.get("id")] = to_time(entry.get("timestamp")) or time.time()
        elif entry.get("type") == "user":
            if is_prompt(entry, content):
                prompt_at = to_time(entry.get("timestamp")) or prompt_at
                last_kind = "prompt"
            elif any(isinstance(p, dict) and p.get("type") == "tool_result" for p in content):
                last_kind = "result"
            for part in content:
                if isinstance(part, dict) and part.get("type") == "tool_result" and part.get("tool_use_id") in launched:
                    found = re.search(r"agentId: (\w+)", json.dumps(part.get("content")))
                    if found:
                        agent_of[part["tool_use_id"]] = found.group(1)
    now = time.time()
    open_jobs = [at for tid, at in launched.items() if tid not in done and agent_of.get(tid) not in done]
    pending = sum(1 for at in open_jobs if now - at < PENDING_MAX)
    return context, last, ids, pending, len(open_jobs) - pending, prompt_at, last_kind in ("tool", "result", "prompt")


def state_path(sid):
    return os.path.join(STATE_DIR, re.sub(r"[^A-Za-z0-9-]", "_", sid or "none") + ".json")


def load_state(sid, ids):
    try:
        with open(state_path(sid), encoding="utf-8") as fh:
            return fix_state(json.load(fh))
    except (OSError, ValueError):
        pass
    # Reopened conversation under a new id: inherit the state that shares message ids.
    if ids and os.path.isdir(STATE_DIR):
        known = set(ids)
        for path in glob.glob(os.path.join(STATE_DIR, "*.json")):
            try:
                with open(path, encoding="utf-8") as fh:
                    old = json.load(fh)
            except (OSError, ValueError):
                continue
            if known & set(old.get("ids") or []):
                return fix_state(old)
    return fix_state({})


def fix_state(state):
    """Fills missing keys, so a damaged or older state file never silences the guard. A v1.1 "locked" record
    becomes the matching lock type."""
    if not isinstance(state, dict):
        state = {}
    for key, value in (("long", False), ("notified", []), ("ids", [])):
        state.setdefault(key, value)
    if not isinstance(state.get("locks"), dict):
        state["locks"] = {}
    if state.pop("locked", False):
        reason = state.get("reason") or ""
        kind = "finish" if reason == "finish session" else "break" if reason.startswith("break") else "context"
        state["locks"].setdefault(kind, {"reason": reason, "at": time.time()})
    state.pop("reason", None)
    return state


def lock(state, kind, reason, **extra):
    state["locks"][kind] = dict(extra, reason=reason, at=time.time())


def closed(state):
    """The permanent lock's reason, or None."""
    for kind in ("context", "finish", "break"):
        if kind in state["locks"]:
            return state["locks"][kind].get("reason") or kind
    return None


def closed_kind(state):
    for kind in ("context", "finish", "break"):
        if kind in state["locks"]:
            return kind
    return "context"


def safe_name(sid):
    return re.sub(r"[^A-Za-z0-9-]", "_", sid or "none")


def started(sid):
    """The start marker SessionStart wrote for this session with this script ({"self", "rules"}), or None: then
    the session started before this install or update (restart lock)."""
    try:
        with open(os.path.join(STATE_DIR, "started", safe_name(sid)), encoding="utf-8") as fh:
            mark = json.load(fh)
    except (OSError, ValueError):
        return None
    return mark if isinstance(mark, dict) and mark.get("self") == SELF else None


def mark_started(sid, rules):
    """rules: whether this session got the Session Guard rules; if not, its next active prompt gets them."""
    path = os.path.join(STATE_DIR, "started", safe_name(sid))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"self": SELF, "rules": rules}, fh)


def old_rules_flag(sid):
    try:
        with open(os.path.join(STATE_DIR, "started", safe_name(sid)), encoding="utf-8") as fh:
            return bool(json.load(fh).get("rules"))
    except (OSError, ValueError, AttributeError):
        return False


# --- Session Follow -------------------------------------------------------------
def follow_path(name):
    return os.path.join(FOLLOW_DIR, name + ".json")


def readings():
    """{window: {used_percent, resets_at, read_at, source}} of the windows with a current reading."""
    found = {}
    for name, _ in WINDOWS:
        try:
            with open(follow_path(name), encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict) or not data:
            continue
        if not isinstance(data.get("resets_at"), (int, float)) or data["resets_at"] <= time.time():
            write_follow(name, {})  # the window reset: the old number no longer holds
            continue
        found[name] = data
    return found


def write_follow(name, data):
    os.makedirs(FOLLOW_DIR, exist_ok=True)
    with open(follow_path(name), "w", encoding="utf-8") as fh:
        json.dump(data, fh)


def save_reading(name, percent, resets, source):
    if isinstance(resets, str):
        resets = to_time(resets)
    if not isinstance(percent, (int, float)) or not isinstance(resets, (int, float)):
        return
    if resets > 1e11:  # milliseconds
        resets /= 1000.0
    write_follow(name, {"used_percent": float(percent), "resets_at": float(resets), "read_at": time.time(), "source": source})


def usage_windows(obj, depth=0):
    """The plan.windows list of a usage tool result, wherever the response wraps it (text blocks, JSON text)."""
    if depth > 6:
        return None
    if isinstance(obj, str):
        try:
            return usage_windows(json.loads(obj), depth + 1)
        except ValueError:
            return None
    if isinstance(obj, dict):
        if isinstance(obj.get("windows"), list):
            return obj["windows"]
        obj = list(obj.values())
    if isinstance(obj, list):
        for item in obj:
            found = usage_windows(item, depth + 1)
            if found is not None:
                return found
    return None


def take_usage(response):
    """Saves the 5-hour and weekly (all models) readings of a desktop usage tool result."""
    for win in usage_windows(response) or []:
        if not isinstance(win, dict):
            continue
        label = str(win.get("label") or "").lower()
        name = "five_hour" if label.startswith("5-hour") else "weekly" if "weekly" in label and "all models" in label else None
        if name:
            save_reading(name, win.get("percentUsed"), win.get("resetsAt"), "desktop")


def quota_hit():
    """(window, reading) past its stop threshold with the reset at least 45 minutes away, or None."""
    for name, reading in sorted(readings().items()):
        if reading.get("used_percent", 0) >= STOP_AT[name] and reading["resets_at"] - time.time() >= RESET_MARGIN:
            return name, reading
    return None


def quota_cleared(info):
    """A quota lock ends at its reset, or when a newer reading of its window is below the stop threshold."""
    if time.time() >= info.get("resets_at", 0):
        return True
    reading = readings().get(info.get("window"))
    return bool(reading and reading.get("read_at", 0) > info.get("at", 0) and reading.get("used_percent", 0) < STOP_AT[info["window"]])


def clock(epoch):
    return datetime.fromtimestamp(epoch).strftime("%H:%M" if epoch - time.time() < 20 * 3600 else "%a %H:%M")


def quota_text(info, name="quota_block"):
    window = info.get("window") if info.get("window") in dict(WINDOWS) else "five_hour"
    return msg(name, percent=info.get("percent", 0), window=msg("window_" + window)[len("Session Guard: "):],
               time=clock(info.get("resets_at", 0)))


def quota_lock(state, hit):
    name, reading = hit
    lock(state, "quota", "quota", window=name, percent=round(reading["used_percent"]), resets_at=reading["resets_at"])


def needs_reading(state, during_work):
    """Desktop app only (its status line does not run; the terminal's status line keeps the files fresh)."""
    if PROJECT_MODE or os.environ.get("CLAUDE_CODE_ENTRYPOINT") != "claude-desktop":
        return False
    found, now = readings(), time.time()
    high = any(r.get("used_percent", 0) >= ASK_AT[n] for n, r in found.items())
    newest = max([r.get("read_at", 0) for r in found.values()] or [0])
    if not high and now - newest < READING_MAX_AGE:
        return False
    if during_work and now - state.get("asked_at", 0) < ASK_STEP:
        return False
    state["asked_at"] = now
    return True


# --- status line ------------------------------------------------------------------
def python_cmd(path, args):
    path = path.replace("\\", "/")
    return ('if [ "$OS" = "Windows_NT" ]; then python -I "%s" %s; else python3 -I "%s" %s; fi' % (path, args, path, args))


def statusline_command():
    return python_cmd(SELF, "--statusline") + " # " + STATUSLINE_SIGN


def load_settings():
    """~/.claude/settings.json as a dict ({} when missing), or None when it cannot be parsed (never overwritten)."""
    try:
        with open(SETTINGS, encoding="utf-8-sig") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def save_settings(data):
    os.makedirs(CLAUDE_DIR, exist_ok=True)
    with open(SETTINGS, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def signed(entry):
    return isinstance(entry, dict) and STATUSLINE_SIGN in str(entry.get("command") or "")


def install_statusline():
    """Writes Session Guard's signed status line, or replaces an older signed one. Another tool's status line is
    left alone: then the terminal gives no quota readings (the desktop app reads its usage tool anyway)."""
    settings = load_settings()
    if settings is None:
        return "settings.json cannot be read: not changed"
    current = settings.get("statusLine")
    if current and not signed(current):
        return "another status line is in place: not changed (no quota readings in the terminal)"
    entry = dict(current) if current else {"type": "command"}
    entry["command"] = statusline_command()
    if current == entry:
        return "already in place"
    settings["statusLine"] = entry
    save_settings(settings)
    return "installed"


def uninstall_statusline():
    settings = load_settings()
    if settings is None:
        return "settings.json cannot be read: not changed"
    if not signed(settings.get("statusLine")):
        return "no Session Guard status line"
    settings.pop("statusLine")
    save_settings(settings)
    return "removed"


def refresh_statusline():
    """After a plugin update the script path changes: point the signed status line at this copy."""
    settings = load_settings()
    if settings and signed(settings.get("statusLine")) and settings["statusLine"].get("command") != statusline_command():
        settings["statusLine"]["command"] = statusline_command()
        save_settings(settings)


def statusline():
    """Terminal status line: saves the quota numbers Claude Code passes in (zero tokens) and shows them."""
    raw = sys.stdin.buffer.read()
    try:
        limits = json.loads(raw.decode("utf-8", errors="replace") or "{}").get("rate_limits") or {}
        for source, name in (("five_hour", "five_hour"), ("seven_day", "weekly")):
            win = limits.get(source)
            if isinstance(win, dict):
                save_reading(name, win.get("used_percentage"), win.get("resets_at"), "statusline")
    except Exception:
        pass
    found = readings()
    out(" · ".join("%s %d%%" % (label, found[name]["used_percent"]) for name, label in WINDOWS if name in found)
        or "Session Guard")
    return 0


def save_state(sid, state, ids):
    if ids:
        state["ids"] = ids[-KEEP_IDS:]
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(state_path(sid), "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False)


def step_for(context, long_mode):
    for step in (LONG_STEPS if long_mode else NORMAL_STEPS):
        if context is not None and context >= step[0]:
            return step
    return None


def k(n):
    return "unknown" if n is None else "%dk" % round(n / K)


def emit(data):
    sys.stdout.write(json.dumps(data, ensure_ascii=True))
    return 0


def out(text):
    """Prints UTF-8 whatever the console code page is (file paths and CLAUDE.md lines may be non-ASCII)."""
    sys.stdout.buffer.write((text + "\n").encode("utf-8"))
    sys.stdout.flush()


# --- projects, setup gate, project files ------------------------------------
def key(path):
    return os.path.normcase(os.path.abspath(path))


def inside(path, parent):
    p, q = key(path), key(parent)
    return p == q or p.startswith(q.rstrip("\\/") + os.sep)


def usable_root(path):
    """The folder as a project root, or None for home, a drive root, ~/.claude and system folders."""
    if not path or not os.path.isdir(path):
        return None
    path = os.path.abspath(path)
    if key(path) == key(HOME) or os.path.dirname(path) == path or inside(path, CLAUDE_DIR):
        return None
    for name in ("SystemRoot", "ProgramFiles", "ProgramFiles(x86)", "ProgramData"):
        if os.environ.get(name) and inside(path, os.environ[name]):
            return None
    return path


def project_root(inp):
    """CLAUDE_PROJECT_DIR (where the session started); without it, the nearest folder up with CLAUDE.md or .git."""
    if os.environ.get("CLAUDE_PROJECT_DIR"):
        return usable_root(os.environ["CLAUDE_PROJECT_DIR"])
    start = os.path.abspath(inp.get("cwd") or os.getcwd())
    path = start
    while usable_root(path):
        if exists_ci(path, "CLAUDE.md") or os.path.exists(os.path.join(path, ".git")):
            return path
        path = os.path.dirname(path)
    return usable_root(start)


def main_checkout(root):
    """A git worktree (.git is a file: "gitdir: <main>/.git/worktrees/<name>") shares the review of its main
    checkout: same repository, same CLAUDE.md. Anything else is its own main checkout."""
    try:
        with open(os.path.join(root, ".git"), encoding="utf-8") as fh:
            found = re.match(r"gitdir:\s*(.+)", fh.read().strip())
    except OSError:
        return root
    if found:
        gitdir = os.path.abspath(os.path.join(root, found.group(1).strip()))
        if os.path.basename(os.path.dirname(gitdir)) == "worktrees" and os.path.isdir(os.path.dirname(os.path.dirname(gitdir))):
            return os.path.dirname(os.path.dirname(os.path.dirname(gitdir)))
    return root


def exists_ci(base, rel):
    """The real path of base/rel matched without regard to letter case, or None."""
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


def settings_hooks(path):
    """Hook commands of a settings file, Session Guard's own excluded."""
    try:
        with open(path, encoding="utf-8-sig") as fh:
            hooks = json.load(fh).get("hooks") or {}
    except (OSError, ValueError, AttributeError):
        return []
    return ["%s: %s" % (event, h.get("command") or h.get("type")) for event, groups in hooks.items()
            if isinstance(groups, list) for g in groups if isinstance(g, dict)
            for h in g.get("hooks", []) if isinstance(h, dict) and "session_guard.py" not in (h.get("command") or "")]


def review_items(root, is_global=False):
    """What a conflict review must read: CLAUDE.md files, skills, commands, agents, scripts, hook settings."""
    if is_global:
        claude, files = CLAUDE_DIR, [GLOBAL_MD]
        folders = ("skills", "commands", "agents", "scripts", "output-styles", "rules")
    else:
        claude = os.path.join(root, ".claude")
        files = [exists_ci(root, n) for n in ("CLAUDE.md", "CLAUDE.local.md", ".claude/CLAUDE.md")]
        folders = ("skills", "commands", "agents", "output-styles", "rules")
    items = [f for f in files if f and os.path.isfile(f)]
    if is_global:  # auto-memory files are read at session start too
        items += sorted(glob.glob(os.path.join(CLAUDE_DIR, "projects", "*", "memory", "*.md")))
    for folder in folders:
        path = os.path.join(claude, folder)
        if os.path.isdir(path):
            # The project copy's own skills are Session Guard; an old copy in ~/.claude is a conflict to find.
            items += [os.path.join(path, n) for n in sorted(os.listdir(path))
                      if is_global or not n.startswith("session-guard")]
    items += [os.path.join(claude, n) for n in ("settings.json", "settings.local.json")
              if settings_hooks(os.path.join(claude, n))]
    if not is_global:
        items += similar_files(root)
    return items


SIMILAR = ("AGENTS.md", "GEMINI.md", ".cursorrules", ".cursor/rules", ".windsurfrules", ".clinerules",
           ".github/copilot-instructions.md", "CONTEXT.md", "MEMORY.md")
SIMILAR_START = ("handoff", "hand-off", "hand_off", "notes", "todo", "progress")
OWN_FILES = ("status.md", "status_archive.md")


def similar_files(root):
    """Files that do the job of CLAUDE.md / STATUS / ARCHIVE under another name (other tools' rule files, hand-off,
    notes, to-do, progress or status files), in the project root and docs/. Read in full by the review."""
    found = [exists_ci(root, n) for n in SIMILAR]
    for folder in (root, os.path.join(root, "docs")):
        try:
            names = sorted(os.listdir(folder))
        except OSError:
            continue
        for n in names:
            low, ext = n.lower(), os.path.splitext(n)[1].lower()
            if ext not in ("", ".md", ".txt") or not os.path.isfile(os.path.join(folder, n)):
                continue
            if folder != root and low in OWN_FILES:
                continue
            if low.startswith(SIMILAR_START) or "status" in low:
                found.append(os.path.join(folder, n))
    seen, result = set(), []
    for path in found:
        if path and key(path) not in seen:
            seen.add(key(path))
            result.append(path)
    return result


def load_config():
    try:
        with open(CONFIG, encoding="utf-8") as fh:
            cfg = json.load(fh)
    except (OSError, ValueError):
        cfg = {}
    if not isinstance(cfg, dict):
        cfg = {}
    if not isinstance(cfg.get("projects"), dict):
        cfg["projects"] = {}
    return cfg


def save_config(cfg):
    os.makedirs(os.path.dirname(CONFIG), exist_ok=True)
    with open(CONFIG, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False, indent=1)


def marker_path(root):
    return os.path.join(root, ".claude", "session-guard", "setup.json")


def write_marker(root, status):
    os.makedirs(os.path.dirname(marker_path(root)), exist_ok=True)
    with open(marker_path(root), "w", encoding="utf-8") as fh:
        json.dump({"rules": RULES_VERSION, "status": status, "date": datetime.now().strftime("%Y-%m-%d")}, fh)
        fh.write("\n")


def activation(root):
    """'active', 'setup' (one-time setup missing or rules changed), 'review' (project not checked) or 'declined'."""
    if PROJECT_MODE:
        if root is None:
            return "active"
        try:
            with open(marker_path(root), encoding="utf-8") as fh:
                marker = json.load(fh)
        except (OSError, ValueError):
            marker = {}
        if not isinstance(marker, dict):
            marker = {}
        if marker.get("status") == "declined":
            return "declined"
        if marker.get("status") == "approved" and marker.get("rules") == RULES_VERSION:
            return "active"
        if review_items(root):
            return "review"
        write_marker(root, "approved")
        return "active"
    cfg = load_config()
    if cfg.get("rules") != RULES_VERSION or cfg.get("global") not in ("approved", "declined"):
        return "setup"
    if cfg["global"] == "declined":
        return "declined"
    if root is None:
        return "active"
    status = cfg["projects"].get(key(main_checkout(root)))
    if status:
        return "active" if status == "approved" else "declined"
    if review_items(root):
        return "review"
    cfg["projects"][key(main_checkout(root))] = "approved"  # nothing to review: a new or empty project
    save_config(cfg)
    return "active"


def command():
    return '%s "%s"%s' % ("python" if os.name == "nt" else "python3", SELF, " --project" if PROJECT_MODE else "")


def inactive(state, root, event="SessionStart"):
    skill = "session-guard-setup" if PROJECT_MODE else "session-guard:setup"
    if state == "setup":
        ctx, user = SETUP_NEEDED % (skill, command()), "setup_pending"
    elif state == "review":
        ctx, user = REVIEW_NEEDED % (root, skill, command()), "review_pending"
    else:
        ctx, user = DECLINED % (" in this project" if root else "", skill), "declined"
    data = {"hookSpecificOutput": {"hookEventName": event, "additionalContext": ctx}}
    if event == "SessionStart":
        data["systemMessage"] = msg(user)
    return data


def create_project_files(root):
    """Creates the missing CLAUDE.md, docs/STATUS.md, docs/STATUS_ARCHIVE.md. Existing files are never touched."""
    made = []
    wanted = [("docs/STATUS.md", STATUS_TEMPLATE), ("docs/STATUS_ARCHIVE.md", ARCHIVE_TEMPLATE)]
    if not (exists_ci(root, "CLAUDE.md") or exists_ci(root, ".claude/CLAUDE.md")):
        wanted.insert(0, ("CLAUDE.md", PROJECT_TEMPLATE % os.path.basename(root)))
    for rel, text in wanted:
        if exists_ci(root, rel):
            continue
        path = os.path.join(root, *rel.split("/"))
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "x", encoding="utf-8") as fh:  # "x": never overwrites, even in a race
                fh.write(text)
            made.append(rel)
        except OSError:
            pass
    return made


def ensure_global_block():
    """Writes GENERAL_RULES between the markers of ~/.claude/CLAUDE.md (created if missing); the user's own text
    is kept. Returns 'same', 'updated', 'added' or 'broken' (markers damaged: the file is left alone)."""
    try:
        with open(GLOBAL_MD, encoding="utf-8-sig", newline="") as fh:
            text = fh.read()
    except FileNotFoundError:
        text = ""
    nl = "\r\n" if "\r\n" in text else "\n"
    block = (BLOCK_BEGIN + "\n" + GENERAL_RULES + "\n" + BLOCK_END).replace("\n", nl)
    opener = BLOCK_BEGIN.split(" (")[0]
    starts, ends = text.count(opener), text.count(BLOCK_END)
    if starts == ends == 0:
        new, result = (block + nl + nl + text if text.strip() else block + nl), "added"
    elif starts == ends == 1 and text.index(opener) < text.index(BLOCK_END):
        a, b = text.index(opener), text.index(BLOCK_END) + len(BLOCK_END)
        if text[a:b] == block:
            return "same"
        new, result = text[:a] + block + text[b:], "updated"
    else:
        return "broken"
    os.makedirs(CLAUDE_DIR, exist_ok=True)
    with open(GLOBAL_MD, "w", encoding="utf-8", newline="") as fh:
        fh.write(new)
    return result


def clean_states():
    """Deletes Session Guard's own per-session records (long mode, locks, notices) and start markers unused for
    30 days, so the reopened-conversation search stays fast. A locked session's record is kept: a reopened old
    session stays locked. Nothing else is ever deleted here (no project, conversation or setup file)."""
    for path in glob.glob(os.path.join(STATE_DIR, "*.json")) + glob.glob(os.path.join(STATE_DIR, "started", "*")):
        if time.time() - os.path.getmtime(path) <= STATE_MAX_AGE:
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            locked = os.path.dirname(path) == STATE_DIR and bool(closed(fix_state(data)))
        except (OSError, ValueError):
            locked = False
        if not locked:
            os.remove(path)


def start_context(root):
    """What a session gets when Session Guard is active: rules, general rules, project files, STATUS.md."""
    parts = [RULES]
    if PROJECT_MODE:
        parts.append(GENERAL_RULES)  # no global CLAUDE.md here (cloud)
    else:
        block = ensure_global_block()
        if block in ("added", "updated"):
            parts.append("The Session Guard block of ~/.claude/CLAUDE.md was refreshed; it reads:\n" + GENERAL_RULES)
        elif block == "broken":
            parts.append("WARNING: the session-guard markers in ~/.claude/CLAUDE.md are damaged, so the general rules "
                         "were not refreshed. Tell the user; the setup (skill session-guard:setup) repairs it.")
        refresh_statusline()
    locale, missing = missing_translations() if notice_locale() else (None, [])
    if missing and len(missing) < len(MESSAGES):  # a newer version added notices; a new language is the user's call
        parts.append("Session Guard notices without a translation for the language %s: %s. Translate them now, as "
                     "the setup skill says (\"Notices\"), without mentioning it." % (locale, ", ".join(missing)))
    # A worktree gets the files from its main checkout through git, never a second, diverging copy.
    made = create_project_files(root) if root and main_checkout(root) == root else []
    if made:
        parts.append("Session Guard created %s in %s (missing files only; existing files are never touched)%s." % (
            ", ".join(made), root, ". Commit them with your work" if PROJECT_MODE else ""))
    status = exists_ci(root, "docs/STATUS.md") if root else None
    if status:
        with open(status, encoding="utf-8", errors="replace") as fh:
            parts.append("docs/STATUS.md (already loaded, do not read again):\n\n" + fh.read()[:8000])
    elif root is None:
        parts.append("Not in a project folder (home, drive root, ~/.claude or a system folder): no project files.")
    return "\n\n".join(parts)


def quota_start(sid, hit):
    """A new session while the shared quota reading is past its stop threshold: locked at once, nothing created."""
    state = load_state(sid, [])
    quota_lock(state, hit)
    save_state(sid, state, [])
    text = quota_text(state["locks"]["quota"])
    return emit({"systemMessage": text, "hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext":
                 "[Session Guard] QUOTA LOCK: " + text + " Do no work; the user's prompts are blocked until then."}})


def on_session_start(inp):
    if os.path.isdir(STATE_DIR):
        clean_states()
    sid = inp.get("session_id")
    resume = inp.get("source") == "resume"
    root = project_root(inp)
    state = activation(root)
    if state == "declined":
        mark_started(sid, False)
        return 0 if resume else emit(inactive(state, root))
    hit = quota_hit() if not PROJECT_MODE else None
    if hit:
        mark_started(sid, old_rules_flag(sid) if resume else False)
        return quota_start(sid, hit)
    if state != "active":
        mark_started(sid, False)
        return emit(inactive(state, root))
    if resume:
        # The conversation already holds the rules if it got them before; one that started before the install or
        # while setup was pending gets them with its next prompt.
        mark_started(sid, old_rules_flag(sid))
        return 0
    mark_started(sid, True)
    return emit({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": start_context(root)}})


def on_prompt(inp):
    root = project_root(inp)
    act = activation(root)
    if act == "declined":
        return 0
    sid = inp.get("session_id")
    mark = started(sid)
    if mark is None:
        return emit({"decision": "block", "reason": msg("restart_project" if PROJECT_MODE else "restart_plugin")})
    os.utime(os.path.join(STATE_DIR, "started", safe_name(sid)))  # in use: kept by the 30-day cleanup
    context, last, ids, pending, stale, _, _ = read_transcript(inp.get("transcript_path"))
    state = load_state(sid, ids)
    if closed(state):
        save_state(sid, state, ids)
        return emit({"decision": "block", "reason": msg("closed_" + closed_kind(state))})
    quota = state["locks"].get("quota")
    if quota and quota_cleared(quota):
        del state["locks"]["quota"]
        quota = None
    if quota:
        save_state(sid, state, ids)
        return emit({"decision": "block", "reason": quota_text(quota)})
    if act != "active":
        save_state(sid, state, ids)
        return emit(inactive(act, root, "UserPromptSubmit"))

    head = []
    if not mark.get("rules"):  # started while setup or review was pending, or before the install
        head.append(start_context(root))
        mark_started(sid, True)
    prompt = normalize(inp.get("prompt") or "")
    gap = time.time() - last if last else None
    notes, user = [], []
    if has_command(LONG, prompt) and not state.get("long"):
        state["long"] = True
        notes.append("LONG MODE is on for this session (cannot be switched off). Confirm it to the user in one line.")
    long_mode = state.get("long")
    finish = has_command(FINISH, prompt)
    # The status line goes out only together with an instruction: a plain prompt costs no tokens.
    line = "[Session Guard] Context: %s (last reply). Mode: %s. Since last reply: %s.%s" % (
        k(context), "LONG" if long_mode else "normal", "unknown" if gap is None else "%d min" % (gap // 60),
        " Background jobs still running: %d (break rule off)." % pending if pending else "")

    limit = LONG_LIMIT if long_mode else NORMAL_LIMIT
    if context is not None and context >= limit and not finish:
        lock(state, "context", "context %s" % k(context))
        save_state(sid, state, ids)
        return emit({"decision": "block", "reason": msg("hard_limit", k=k(context))})

    if finish:
        lock(state, "finish", "finish session")
        notes.append('FINISH SESSION. Do nothing else the message asks. Only follow skill session-guard "Finish session" '
                      '(hand-off + measurement: python "%s" --report). The session is locked after this reply.' % SELF)
        user.append(msg("finishing"))
    elif not long_mode and not pending and gap is not None and gap > BREAK_SECONDS:
        if context is not None and context > BREAK_CONTEXT:
            lock(state, "break", "break over 1 hour, context %s" % k(context))
            notes.append("BREAK RULE (gap %d min, context %s > 100k). Do NOT run the user's command and do no reading or "
                         'research for it. Only follow skill session-guard "Break hand-off"; copy the command into the note '
                         "word for word; tell the user to continue in a new session. The session is locked after this reply."
                         % (gap // 60, k(context)))
            user.append(msg("break_lock", k=k(context)))
        else:
            notes.append("BREAK REMINDER: gap over 1 hour (%d min), context %s <= 100k. Remind the user in one line, then "
                         "handle the command normally." % (gap // 60, k(context)))
            user.append(msg("break_remind", k=k(context)))

    if stale and not closed(state):
        notes.append(STALE_JOBS % stale)
        if "stale24" not in state["notified"]:
            state["notified"].append("stale24")
            user.append(msg("stale"))

    if not closed(state):
        step = step_for(context, long_mode)
        if step:
            if step[1] not in NOTIFY_ONCE or step[1] not in state["notified"]:
                notes.append(step[2] + (NOTICE_FORM % (step[0] // K) if step[1] not in state["notified"] else ""))
            if step[1] not in state["notified"]:
                state["notified"].append(step[1])
                user.append(msg(step[1]))
        hit = None if PROJECT_MODE else quota_hit()
        if hit:
            quota_lock(state, hit)
            notes.append(QUOTA_STOP % (dict(WINDOWS)[hit[0]], hit[1]["used_percent"], clock(hit[1]["resets_at"])))
            user.append(quota_text(state["locks"]["quota"], "quota_stop"))
        elif needs_reading(state, False):
            notes.append(READ_USAGE)

    save_state(sid, state, ids)
    if not notes and not head:
        return 0
    data = {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                                   "additionalContext": "\n\n".join(head + (["\n".join([line] + notes)] if notes else []))}}
    if user:
        data["systemMessage"] = " ".join(user[:1] + [u[len("Session Guard: "):] for u in user[1:]])
    return emit(data)


def watch(inp):
    """24-hour watch: an async hook (asyncRewake) started with every prompt. The newest prompt's watch replaces the
    older ones (they see a new token within a minute and exit). After 24 hours with no newer prompt, if the
    session is still busy (a tool running, a background job open, or no answer to the prompt), exit code 2 wakes
    Claude with WATCH_WAKE, so the user is told even when no other hook event happens. A finished turn waiting
    for the user is not a bug: then it exits 0 and costs nothing."""
    sid = inp.get("session_id")
    token_path = os.path.join(STATE_DIR, "watch", safe_name(sid))
    token = "%f %d" % (time.time(), os.getpid())
    os.makedirs(os.path.dirname(token_path), exist_ok=True)
    with open(token_path, "w", encoding="utf-8") as fh:
        fh.write(token)
    deadline = time.time() + WATCH_SECONDS
    while time.time() < deadline:
        time.sleep(max(0, min(WATCH_STEP, deadline - time.time())))
        try:
            with open(token_path, encoding="utf-8") as fh:
                if fh.read() != token:
                    return 0  # a newer prompt started its own watch
        except OSError:
            return 0
    if activation(project_root(inp)) != "active" or started(sid) is None:
        return 0
    _, _, ids, pending, stale, _, turn_open = read_transcript(inp.get("transcript_path"))
    if load_state(sid, ids)["locks"] or not (pending or stale or turn_open):
        return 0
    sys.stderr.write(WATCH_WAKE)
    return 2


def on_tool(inp):
    """While Claude works: takes quota readings, tells once when a new threshold is passed, stops the turn at the
    hard limit and 15 minutes after a quota stop."""
    act = activation(project_root(inp))
    if act == "declined":
        return 0
    if inp.get("tool_name") == USAGE_TOOL and not PROJECT_MODE:
        take_usage(inp.get("tool_response"))
    sid = inp.get("session_id")
    if act != "active" or started(sid) is None:
        return 0
    context, _, ids, _, _, prompt_at, _ = read_transcript(inp.get("transcript_path"))
    state = load_state(sid, ids)
    if closed(state):
        return 0
    quota = state["locks"].get("quota")
    if quota:
        if quota_cleared(quota):
            del state["locks"]["quota"]
            save_state(sid, state, ids)
        elif time.time() - quota.get("at", 0) > QUOTA_GRACE:
            text = quota_text(quota, "quota_stopped")
            return emit({"continue": False, "stopReason": text, "systemMessage": text})
        else:
            return 0
    turn = "turn24:%s" % prompt_at
    if prompt_at and time.time() - prompt_at > PENDING_MAX and turn not in state["notified"]:
        state["notified"].append(turn)  # once per user message
        save_state(sid, state, ids)
        return emit({"systemMessage": msg("stale"),
                     "hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": LONG_TURN}})
    step = step_for(context, state.get("long"))
    if step and step[1] not in state["notified"]:
        state["notified"].append(step[1])
        data = {"systemMessage": msg(step[1]),
                "hookSpecificOutput": {"hookEventName": "PostToolUse",
                                       "additionalContext": "[Session Guard] Context: %s (during work). %s%s" % (
                                           k(context), step[2], NOTICE_FORM % (step[0] // K))}}
        if step[4]:
            lock(state, "context", "context %s" % k(context))
            data.update({"continue": False, "stopReason": msg(step[1])})
        save_state(sid, state, ids)
        return emit(data)
    hit = None if PROJECT_MODE else quota_hit()
    if hit:
        quota_lock(state, hit)
        save_state(sid, state, ids)
        return emit({"systemMessage": quota_text(state["locks"]["quota"], "quota_stop"),
                     "hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": "[Session Guard] " +
                         QUOTA_STOP % (dict(WINDOWS)[hit[0]], hit[1]["used_percent"], clock(hit[1]["resets_at"]))}})
    if needs_reading(state, True):
        save_state(sid, state, ids)
        return emit({"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": READ_USAGE}})
    return 0


def report(project_dir):
    """One line: requests, cache read/write, output tokens, largest context of the latest session."""
    cwd = os.path.abspath(project_dir or os.getcwd())
    folder = os.path.join(os.path.expanduser("~"), ".claude", "projects", re.sub(r"[^A-Za-z0-9]", "-", cwd))
    files = glob.glob(os.path.join(folder, "*.jsonl"))
    if not files:
        print("no transcript: " + folder)
        return 1
    path = max(files, key=os.path.getmtime)
    usage, order = {}, []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            msg = entry.get("message") or {}
            if entry.get("type") == "assistant" and isinstance(msg, dict) and msg.get("id") and msg.get("usage"):
                if msg["id"] not in usage:
                    order.append(msg["id"])
                usage[msg["id"]] = msg["usage"]  # the last record holds the final value
    read = write = out = biggest = 0
    for mid in order:
        u = usage[mid]
        r, w = u.get("cache_read_input_tokens") or 0, u.get("cache_creation_input_tokens") or 0
        read, write, out = read + r, write + w, out + (u.get("output_tokens") or 0)
        biggest = max(biggest, r + w + (u.get("input_tokens") or 0))
    print("session=%s requests=%d cache_read=%d cache_write=%d output=%d largest_context=%d" % (
        os.path.basename(path)[:8], len(order), read, write, out, biggest))
    return 0


# --- setup commands (run by skill setup) -------------------------------------
KEYWORDS = re.compile(r"context|bağlam|baglam|token|\b\d{2,3}\s?k\b|hand-?off|devir|mola|\bbreak\b|finish|long mode|"
                      r"uzun mod|status|archive|arşiv|arsiv|session|oturum|threshold|eşik|limit|sınır|compact|"
                      r"reply style|yazım|label|etiket", re.I)
TEXT_EXT = (".md", ".py", ".ps1", ".sh", ".js", ".ts", ".json", ".txt", ".bat", ".cmd")
MAX_HITS = 40


def first_cwd(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
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


def known_projects():
    """Every project folder Claude Code has sessions for (the "cwd" its transcripts start with)."""
    roots = {}
    for folder in glob.glob(os.path.join(CLAUDE_DIR, "projects", "*")):
        for path in sorted(glob.glob(os.path.join(folder, "*.jsonl")), key=os.path.getmtime, reverse=True)[:3]:
            cwd = first_cwd(path)
            if cwd:
                root = usable_root(cwd)
                if root:
                    root = main_checkout(root)
                    roots[key(root)] = root
                break
    return sorted(roots.values(), key=str.lower)


def hits(path):
    """Lines that touch Session Guard topics; text outside the Session Guard block only."""
    if os.path.isfile(path) and os.path.basename(path).lower() in ("settings.json", "settings.local.json"):
        return settings_hooks(path)
    files = [path]
    if os.path.isdir(path):
        files = sorted(os.path.join(d, n) for d, _, names in os.walk(path) for n in names if n.lower().endswith(TEXT_EXT))
    found = []
    for name in files[:30]:
        try:
            with open(name, encoding="utf-8", errors="replace") as fh:
                lines = fh.read().splitlines()
        except OSError:
            continue
        inside_block = False
        for n, line in enumerate(lines, 1):
            if line.startswith(BLOCK_BEGIN.split(" (")[0]):
                inside_block = True
            if not inside_block and KEYWORDS.search(line):
                where = os.path.relpath(name, path) + ":" if name != path else ""
                found.append("%s%d: %s" % (where, n, line.strip()[:160]))
            if line.startswith(BLOCK_END):
                inside_block = False
    return found[:MAX_HITS] + (["... more lines: read the file"] if len(found) > MAX_HITS else [])


def block_state():
    try:
        with open(GLOBAL_MD, encoding="utf-8-sig") as fh:
            text = fh.read()
    except OSError:
        return "missing (setup creates it)"
    starts, ends = text.count(BLOCK_BEGIN.split(" (")[0]), text.count(BLOCK_END)
    return {(0, 0): "present, no Session Guard block yet", (1, 1): "present, Session Guard block in place"}.get(
        (starts, ends), "present, Session Guard markers DAMAGED (fix them by hand first)")


def scan(target):
    cfg = load_config()
    if target:
        root = os.path.abspath(target)
        groups = [("PROJECT", root, False)]
    else:
        groups = [("GLOBAL", CLAUDE_DIR, True)] + [("PROJECT", p, False) for p in known_projects()]
    lines = []
    for label, root, is_global in groups:
        if is_global:
            lines.append("GLOBAL %s  [CLAUDE.md: %s]" % (root, block_state()))
        else:
            lines.append("PROJECT %s  [review: %s]" % (root, cfg["projects"].get(key(root), "not done")))
        items = review_items(root, is_global)
        similar = set() if is_global else {key(p) for p in similar_files(root)}
        if not items:
            lines.append("  nothing to review")
        for item in items:
            if key(item) in similar:
                lines.append("  %s%s  [same job as CLAUDE.md / STATUS / ARCHIVE: read in full]" % (item, "/" if os.path.isdir(item) else ""))
                continue
            lines.append("  " + item + ("/" if os.path.isdir(item) else ""))
            lines += ["      " + h for h in hits(item)]
    out("\n".join(lines))
    return 0


def record(args):
    """--setup approved|declined [--all | folder...]: no folder = global; --all = global + every known project."""
    status, rest = (args[0] if args else ""), args[1:]
    if status not in ("approved", "declined"):
        out("usage: --setup approved|declined [--all | project_folder ...]")
        return 2
    folders = [os.path.abspath(f) for f in rest if f != "--all"]
    if PROJECT_MODE:
        for root in folders or [os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()]:
            write_marker(root, status)
            out("recorded %s for %s (commit .claude/session-guard/setup.json)" % (status, root))
        return 0
    cfg = load_config()
    if not folders or "--all" in rest:
        cfg.update(rules=RULES_VERSION, date=datetime.now().strftime("%Y-%m-%d"))
        cfg["global"] = status
        out("recorded %s: global" % status)
    if "--all" in rest:
        folders += known_projects()
    for root in folders:
        root = main_checkout(root)
        cfg["projects"][key(root)] = status
        out("recorded %s: %s" % (status, root))
    save_config(cfg)
    return 0


def cli(args):
    if args[0] == "--scan":
        return scan(args[1] if len(args) > 1 else None)
    if args[0] == "--install-global":
        result = ensure_global_block()
        out("global CLAUDE.md (%s): Session Guard block %s" % (GLOBAL_MD, result))
        return 1 if result == "broken" else 0
    if args[0] == "--setup":
        return record(args[1:])
    if args[0] == "--print-rules":
        out(RULES + "\n\n" + BLOCK_BEGIN + "\n" + GENERAL_RULES + "\n" + BLOCK_END)
        return 0
    if args[0] == "--print-messages":
        locale, missing = missing_translations()
        out("notice language: %s; notices without a translation: %s" % (locale or "English (nothing to translate)",
                                                                        ", ".join(missing) if missing else "none"))
        out(json.dumps(MESSAGES, ensure_ascii=False, indent=1))
        return 0
    if args[0] == "--set-language":
        return set_language(args[1:])
    if args[0] == "--set-messages":
        return set_messages(args[1]) if len(args) > 1 else 2
    if args[0] == "--create-files":
        root = main_checkout(os.path.abspath(args[1] if len(args) > 1 else os.getcwd()))
        made = create_project_files(root)
        out("project files created in %s: %s" % (root, ", ".join(made) if made else "none (all present)"))
        return 0
    if args[0] == "--install-statusline":
        out("status line (%s): %s" % (SETTINGS, install_statusline()))
        return 0
    if args[0] == "--uninstall-statusline":
        out("status line (%s): %s" % (SETTINGS, uninstall_statusline()))
        return 0
    return 2


def plugin_installed():
    """True when the plugin copy exists under ~/.claude/plugins (then the project copy stays quiet)."""
    pattern = os.path.join(os.path.expanduser("~"), ".claude", "plugins", "**", "session_guard.py")
    return any(os.path.abspath(p) != SELF for p in glob.glob(pattern, recursive=True))


def main():
    global PROJECT_MODE
    args = sys.argv[1:]
    PROJECT_MODE = "--project" in args
    args = [a for a in args if a != "--project"]
    if args[:1] == ["--report"]:
        return report(args[1] if len(args) > 1 else None)
    if args[:1] == ["--statusline"]:
        try:
            return statusline()
        except Exception:  # a status line must never show an error
            return 0
    if args[:1] and args[0] in ("--scan", "--install-global", "--setup", "--print-rules", "--create-files",
                                "--install-statusline", "--uninstall-statusline", "--print-messages", "--set-messages",
                                "--set-language"):
        return cli(args)
    try:
        if PROJECT_MODE and plugin_installed():
            return 0
        inp = json.loads(sys.stdin.buffer.read().decode("utf-8", errors="replace") or "{}")
        event = inp.get("hook_event_name")
        if event == "SessionStart":
            return on_session_start(inp)
        if event == "UserPromptSubmit" and "--watch" in args:
            return watch(inp)
        if event == "UserPromptSubmit":
            return on_prompt(inp)
        if event == "PostToolUse":
            return on_tool(inp)
    except Exception:  # the guard must never break the user's work
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
