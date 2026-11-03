# SESSION-GUARD: 2.0 | component: sg2-manifest | version: 2.0.0
"""Central component manifest (<plugin>/manifest.json) and its check.

Every managed file has an entry: component id, component version, path, file type, Session Guard version, sha256
and dependencies. Files that allow comments (.py, .md, .sh) also carry the signature line
"SESSION-GUARD: 2.0 | component: <id> | version: <v>"; JSON files carry none (no invalid comments): the
manifest covers them. The signature alone proves nothing: verify() checks signature, hash and listing together.
"""
import hashlib
import json
import os
import re

from . import SG_VERSION, SIGNATURE, core

SIGNED_EXT = (".py", ".md", ".sh")
SIGN_RE = re.compile(r"SESSION-GUARD: (\d+\.\d+) \| component: ([\w.-]+) \| version: ([\d.]+)")
IMPORT_RE = re.compile(r"^\s*from (?:\.|sg2\.?)(\w*) import ([\w, ]+)", re.M)
SKIP_DIRS = {"__pycache__"}
NAME = "manifest.json"
TYPES = {".py": "python", ".md": "markdown", ".json": "json", ".sh": "shell"}


def files(plugin_root):
    out = []
    for d, dirs, names in os.walk(plugin_root):
        dirs[:] = [x for x in dirs if x not in SKIP_DIRS]
        for n in names:
            rel = os.path.relpath(os.path.join(d, n), plugin_root).replace("\\", "/")
            if rel != NAME and not n.endswith((".pyc", ".tmp")):
                out.append(rel)
    return sorted(out)


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        h.update(fh.read().replace(b"\r\n", b"\n"))  # git may check text out with CRLF on Windows
    return h.hexdigest()


def signature(path):
    text = core.read_text(path) or ""
    m = SIGN_RE.search(text[:2000])
    return {"sg": m.group(1), "component": m.group(2), "version": m.group(3)} if m else None


def component_id(rel, sig):
    if sig:
        return sig["component"]
    return re.sub(r"[^a-z0-9]+", "-", rel.lower().rsplit(".", 1)[0]).strip("-")


def deps(path, rel, ids_by_module):
    if rel.endswith(".py"):
        text = core.read_text(path) or ""
        out = set()
        for m in IMPORT_RE.finditer(text):
            mods = [m.group(1)] if m.group(1) else ["__init__"] + [x.strip() for x in m.group(2).split(",")]
            for mod in mods:
                if mod in ids_by_module:
                    out.add(ids_by_module[mod])
        return sorted(out)
    if rel.endswith("hooks/hooks.json") or rel.startswith("skills/"):
        return ["sg-entry"]
    return []


def build(plugin_root):
    entries, by_module = [], {}
    sigs = {rel: signature(os.path.join(plugin_root, rel)) for rel in files(plugin_root)}
    for rel, sig in sigs.items():
        if rel.startswith("scripts/sg2/") and rel.endswith(".py"):
            mod = os.path.basename(rel)[:-3]
            by_module["__init__" if mod == "__init__" else mod] = component_id(rel, sig)
    for rel, sig in sigs.items():
        p = os.path.join(plugin_root, rel)
        entries.append({"id": component_id(rel, sig), "version": sig["version"] if sig else SG_VERSION,
                        "path": rel, "type": TYPES.get(os.path.splitext(rel)[1], "file"),
                        "session_guard": SG_VERSION, "sha256": sha(p), "signed": bool(sig),
                        "depends": deps(p, rel, by_module)})
    data = {"session_guard": SG_VERSION, "signature": SIGNATURE, "hash": "sha256 (CRLF read as LF)",
            "components": entries}
    core.write_text(os.path.join(plugin_root, NAME), json.dumps(data, indent=1) + "\n")
    return data


def verify(plugin_root):
    """Problems as text lines; an empty list means every component is listed, signed where possible and intact."""
    data = core.read_json(os.path.join(plugin_root, NAME))
    if not isinstance(data, dict) or not isinstance(data.get("components"), list):
        return ["manifest.json missing or unreadable"]
    problems = []
    if data.get("session_guard") != SG_VERSION:
        problems.append("manifest is for Session Guard %s, code is %s" % (data.get("session_guard"), SG_VERSION))
    listed = {}
    for c in data["components"]:
        listed[c.get("path")] = c
        p = os.path.join(plugin_root, c.get("path") or "")
        if not os.path.isfile(p):
            problems.append("missing: " + str(c.get("path")))
            continue
        if sha(p) != c.get("sha256"):
            problems.append("changed (hash differs): " + c["path"])
        sig = signature(p)
        if c["path"].endswith(SIGNED_EXT):
            if not sig:
                problems.append("no 2.0 signature: " + c["path"])
            elif sig["sg"] != "2.0" or sig["component"] != c["id"] or sig["version"] != c["version"]:
                problems.append("signature does not match the manifest: " + c["path"])
        ids = {d["id"] for d in data["components"]}
        for d in c.get("depends") or []:
            if d not in ids:
                problems.append("%s depends on unknown component %s" % (c["path"], d))
    for rel in files(plugin_root):
        if rel not in listed:
            problems.append("not in the manifest: " + rel)
    plugin = core.read_json(os.path.join(plugin_root, ".claude-plugin", "plugin.json"), {})
    if (plugin or {}).get("version") != SG_VERSION:
        problems.append("plugin.json version %s is not %s" % ((plugin or {}).get("version"), SG_VERSION))
    return problems
