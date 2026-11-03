# SESSION-GUARD: 2.0 | component: sg2-messages | version: 2.0.0
"""Notices the app shows the user directly (Claude never sees them, so it cannot translate them).

The language is chosen once at install (v1.4 mechanism, kept): the desktop app's setting read one time, or the
user's answer. Translations: the user's own (config/messages/<locale>.json) over the plugin's
(<plugin>/messages/<locale>.json). English when none. Placeholders %(name)s / %(name)d must survive translation.
"""
import glob
import os
import re

from . import core

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
    "closed_handed": "Session closed: its tasks were taken over by %(by)s. Continue there or in a new session.",
    "closed_legacy": "This conversation began before its project moved to Session Guard 2.0, so it is locked. "
                     "Open a new session; it gets the current tasks and hand-offs.",
    "closed_home": "This project lives in the %(home)s, not here. Your message was not sent.",
    "finishing": "Finishing session: preparing hand-offs. Later messages will not be delivered.",
    "break_lock": "Break over 1 hour and context %(k)s: command not run, it goes into the hand-off notes.",
    "break_remind": "Break over 1 hour; context %(k)s, continuing in this session.",
    "stale": "Possible bug: something has been active for over 24 hours. Please look into it; if it is stuck, "
             "end the session with \"finish session\".",
    "restart_plugin": "This session started before Session Guard was installed or updated, so it is not protected. "
                      "Your message was not sent. Fully quit Claude (tray included) and reopen it, then continue here.",
    "window_five_hour": "5-hour",
    "window_weekly": "weekly",
    "quota_block": "Quota %(percent)d%% (%(window)s), resets at %(time)s. Your message was not sent. Continue after that.",
    "quota_stopped": "Quota %(percent)d%% (%(window)s), resets at %(time)s. Work stopped. Continue after that.",
    "quota_stop": "Quota %(percent)d%% (%(window)s): finishing the current step, then locked until %(time)s.",
    "setup_pending": "Setup pending: Claude starts it with your next message.",
    "migration_pending": "This project is not on Session Guard 2.0 yet: context, breaks and quota are guarded, tasks "
                         "and hand-offs are not. Its migration runs from session-guard-executive.",
    "migration_paused": "Session Guard 2.0 installation paused (token budget). Your message was not sent. In "
                        "session-guard-executive, type: continue installation",
    "v1_still_installed": "Session Guard 1.x is still installed: 2.0 stays passive until the update finishes "
                          "(no double guard).",
    "status_restored": "STATUS.md task block was edited by hand; Session Guard restored it from the task store.",
}
ENGLISH_ONLY = {"v1_still_installed"}
TRANSLATABLE = [n for n in MESSAGES if n not in ENGLISH_ONLY]
PLACEHOLDER = re.compile(r"%\((\w+)\)[sd]")
PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_cache = {}


def own_dir():
    return os.path.join(core.state_dir(), "config", "messages")


def valid(name, text):
    return (isinstance(text, str) and bool(text.strip()) and name in MESSAGES
            and sorted(PLACEHOLDER.findall(text)) == sorted(PLACEHOLDER.findall(MESSAGES[name])))


def _load(path):
    data = core.read_json(path, {})
    return data if isinstance(data, dict) else {}


def translations(locale):
    if locale not in _cache:
        found = {n: t for n, t in _load(os.path.join(PLUGIN_ROOT, "messages", locale + ".json")).items() if valid(n, t)}
        found.update({n: t for n, t in _load(os.path.join(own_dir(), locale + ".json")).items() if valid(n, t)})
        _cache[locale] = found
    return _cache[locale]


def locale():
    """The chosen language; English in the terminal. UNVERIFIED: CLAUDE_CODE_ENTRYPOINT is not documented."""
    if not core.in_cloud() and os.environ.get("CLAUDE_CODE_ENTRYPOINT") != "claude-desktop":
        return None
    return core.load_config().get("locale")


def msg(name, **values):
    loc = locale()
    text = translations(loc).get(name) if loc and name not in ENGLISH_ONLY else None
    if text:
        try:
            return "Session Guard: " + text % values
        except (KeyError, TypeError, ValueError):
            pass
    return "Session Guard: " + MESSAGES[name] % values


def missing(loc):
    return [n for n in TRANSLATABLE if n not in translations(loc)] if loc else []


def app_locale():
    """The Claude desktop app's language, read once at install (from v1.4; the file is undocumented)."""
    places = []
    for base in (os.environ.get("LOCALAPPDATA"), os.environ.get("APPDATA"),
                 os.path.join(core.home(), "Library", "Application Support"), os.path.join(core.home(), ".config")):
        if base:
            places += glob.glob(os.path.join(base, "Claude-Data", "*", "config.json"))
            places += [os.path.join(base, n, "config.json") for n in ("Claude", "Claude-3p")]
    for p in sorted((p for p in places if os.path.isfile(p)), key=os.path.getmtime, reverse=True):
        found = _load(p).get("locale")
        if isinstance(found, str) and re.match(r"^[A-Za-z]{2,3}([-_][A-Za-z0-9]{2,8})*$", found):
            return found.lower().replace("_", "-")
    return None


def set_locale(code):
    cfg = core.load_config()
    code = (code or "").lower().replace("_", "-")
    if not code or code in ("en", "english") or code.startswith("en-"):
        cfg.pop("locale", None)
    else:
        cfg["locale"] = code
    core.save_config(cfg)
    _cache.clear()
    return cfg.get("locale")


def store_translation(loc, data):
    stored = {n: t for n, t in _load(os.path.join(own_dir(), loc + ".json")).items() if valid(n, t)}
    bad = [n for n in data if n in TRANSLATABLE and not valid(n, data[n])]
    stored.update({n: t for n, t in data.items() if n in TRANSLATABLE and valid(n, t)})
    core.write_json(os.path.join(own_dir(), loc + ".json"), stored)
    _cache.clear()
    return bad
