#!/usr/bin/env python3
"""Build dashboard.html from local Claude Code config + cached cloud data.

Fast path (no network): reads ~/.claude for skills, plugins, hooks, scheduled
tasks, instruction files, MCP servers. Cloud routines come from routines.json,
which refresh_cloud() fills by running `claude -p` headlessly (your login stays
inside Claude Code; nothing is copied). Hand-maintained bits live in static.json.
"""
import json, re, subprocess, sys, time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
CLAUDE = Path.home() / ".claude"
OUT = HERE / "dashboard.html"
ROUTINES_JSON = HERE / "routines.json"
STATIC_JSON = HERE / "static.json"
TEMPLATE = HERE / "template.html"

def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def frontmatter(path):
    """Return (name, description) from a SKILL.md YAML frontmatter."""
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return None, None
    m = re.match(r"---\n(.*?)\n---", text, re.S)
    if not m:
        return None, None
    fm = m.group(1)
    def get(k):
        mm = re.search(rf"^{k}:\s*(.*)$", fm, re.M)
        if not mm: return None
        v = mm.group(1).strip()
        if v.startswith(("'", '"')) and v.endswith(v[0]) and len(v) > 1: v = v[1:-1]
        return v
    return get("name"), get("description")

def read_json(p, default):
    try:
        return json.loads(Path(p).read_text())
    except (OSError, json.JSONDecodeError):
        return default

def personal_skills():
    out = []
    for d in sorted((CLAUDE / "skills").glob("*/")):
        if d.name == "synced" or not (d / "SKILL.md").exists():
            continue  # 'synced' holds claude.ai skills mirrored by the desktop app; see claude_ai_skills()
        name, desc = frontmatter(d / "SKILL.md")
        out.append({"name": name or d.name, "desc": desc or "", "invoke": f"/{name or d.name}", "path": str(d)})
    return out

def claude_ai_skills(static_fallback):
    """Skills enabled on claude.ai, mirrored to ~/.claude/skills/synced/<bucket>/<skill>/ by the desktop app."""
    found = {}
    for sk in (CLAUDE / "skills/synced").glob("*/*/SKILL.md"):
        name, desc = frontmatter(sk)
        name = name or sk.parent.name
        found[name] = {"name": name, "desc": (desc or "")[:200], "invoke": f"/{name}", "path": str(sk.parent)}
    if not found:
        return static_fallback
    return sorted(found.values(), key=lambda s: s["name"])

def plugins(settings):
    inst = read_json(CLAUDE / "plugins/installed_plugins.json", {}).get("plugins", {})
    enabled = settings.get("enabledPlugins", {})
    out, hooks = [], []
    for key, entries in inst.items():
        e = entries[0] if entries else {}
        root = Path(e.get("installPath", ""))
        manifest = read_json(root / ".claude-plugin/plugin.json", {})
        skills = []
        for sd in sorted((root / "skills").glob("*/")):
            if (sd / "SKILL.md").exists():
                sname, sdesc = frontmatter(sd / "SKILL.md")
                skills.append({"name": sname or sd.name, "desc": (sdesc or "")[:160], "path": str(sd)})
        name, market = key.split("@", 1) if "@" in key else (key, "")
        extra = []
        if (root / ".mcp.json").exists():
            extra.append("Ships MCP server(s): " + ", ".join(read_json(root / ".mcp.json", {}).get("mcpServers", {}).keys()))
        for hp in [root / "hooks/hooks.json", root / ".claude-plugin/hooks.json"]:
            for event, groups in read_json(hp, {}).get("hooks", {}).items():
                for g in groups:
                    for h in g.get("hooks", []):
                        hooks.append({"name": f"{event} → {g.get('matcher','*')}", "cmd": (h.get("command") or "")[:90], "where": f"{name} plugin", "desc": h.get("statusMessage", "")})
        out.append({"name": name, "market": market, "version": str(e.get("version", ""))[:12],
                    "updated": (e.get("lastUpdated") or e.get("installedAt") or "")[:10],
                    "status": "on" if enabled.get(key, False) else "off",
                    "desc": (manifest.get("description") or "")[:220], "skills": skills, "extra": " ".join(extra)})
    return out, hooks

def settings_hooks(settings):
    out = []
    for event, groups in settings.get("hooks", {}).items():
        for g in groups:
            for h in g.get("hooks", []):
                out.append({"name": f"{event} → {g.get('matcher','*')}", "cmd": h.get("command", ""), "where": "~/.claude/settings.json", "desc": ""})
    return out

def scheduled_tasks(cached):
    out = []
    for d in sorted((CLAUDE / "scheduled-tasks").glob("*/")):
        name, desc = frontmatter(d / "SKILL.md")
        name = name or d.name
        c = cached.get(name, {})
        out.append({"name": name, "desc": desc or "", "cron": c.get("cronExpression", ""), "when": c.get("schedule", ""),
                    "lastRun": c.get("lastRunAt", ""), "nextRun": c.get("nextRunAt", ""), "enabled": c.get("enabled", True),
                    "path": str(d / "SKILL.md")})
    return out

def instructions():
    out = []
    root = CLAUDE / "CLAUDE.md"
    files = [root] + [CLAUDE / m for m in re.findall(r"^@(\S+\.md)", root.read_text(errors="replace"), re.M)] if root.exists() else []
    for f in files:
        if not f.exists(): continue
        text = f.read_text(errors="replace")
        h = re.search(r"^#\s+(.+)$", text, re.M)
        out.append({"name": f.name, "desc": (h.group(1) if h else "").strip()[:120], "lines": text.count("\n"), "dir": str(CLAUDE), "file": f.name})
    return out

def local_mcp():
    servers = read_json(Path.home() / ".claude.json", {}).get("mcpServers", {})
    return [{"name": k, "status": "on", "note": "local (~/.claude.json)"} for k in servers]

ISSUES = CLAUDE / "skill-issues.jsonl"
DISMISSED = CLAUDE / "skill-issues.dismissed.jsonl"

def read_jsonl(p):
    out = []
    if not Path(p).exists(): return out
    for i, line in enumerate(Path(p).read_text(errors="replace").splitlines()):
        line = line.strip()
        if not line: continue
        try:
            o = json.loads(line); o.setdefault("id", f"line{i}"); out.append(o)
        except json.JSONDecodeError:
            out.append({"id": f"bad{i}", "skill": "skill-issues", "summary": "unparseable line in skill-issues.jsonl", "detail": line[:160], "ts": ""})
    return out

def skill_issues():
    """Issues logged by the skill-issues skill, newest first."""
    return sorted(read_jsonl(ISSUES), key=lambda o: o.get("ts", ""), reverse=True)

def dismiss_issue(issue_id):
    """Move one line from skill-issues.jsonl to skill-issues.dismissed.jsonl. Returns True if found."""
    if not ISSUES.exists(): return False
    keep, gone = [], []
    for line in ISSUES.read_text(errors="replace").splitlines():
        try: oid = json.loads(line).get("id")
        except (json.JSONDecodeError, AttributeError): oid = None
        (gone if line.strip() and oid == issue_id else keep).append(line)
    if not gone: return False
    with DISMISSED.open("a") as f:
        for line in gone: f.write(json.dumps({**json.loads(line), "dismissed_at": now_iso()}) + "\n")
    ISSUES.write_text("\n".join(keep) + ("\n" if keep else ""))
    return True

SILENCED = HERE / "silenced.json"

def silenced():
    return read_json(SILENCED, {"keys": []}).get("keys", [])

def set_silenced(key, on):
    keys = [k for k in silenced() if k != key]
    if on: keys.append(key)
    SILENCED.write_text(json.dumps({"keys": keys, "updated_at": now_iso()}, indent=1))
    return keys

def refresh_cloud(timeout=300):
    """Run `claude -p` headlessly to list cloud routines; write routines.json. Returns (ok, message)."""
    prompt = (HERE / "cloud-prompt.txt").read_text()
    t0 = time.time()
    try:
        model = read_json(STATIC_JSON, {}).get("cloud_model", "claude-sonnet-5")
        r = subprocess.run(["claude", "-p", prompt, "--allowedTools", "RemoteTrigger", "--model", model, "--output-format", "json"],
                           capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return False, "claude CLI not found on PATH"
    except subprocess.TimeoutExpired:
        return False, f"claude -p timed out after {timeout}s"
    if r.returncode != 0:
        return False, f"claude -p exit {r.returncode}: {r.stderr[-400:]}"
    try:
        result = json.loads(r.stdout).get("result", "")
        body = re.sub(r"^```(?:json)?|```$", "", result.strip(), flags=re.M).strip()
        routines = json.loads(body)
        assert isinstance(routines, list)
    except Exception as ex:  # noqa: BLE001 - surface whatever Claude returned
        return False, f"could not parse routines from claude output: {ex}; head={r.stdout[:200]!r}"
    ROUTINES_JSON.write_text(json.dumps({"fetched_at": now_iso(), "routines": routines}, indent=1))
    return True, f"{len(routines)} routines in {time.time()-t0:.0f}s"

def validate_skill(text, dirname):
    """Check a SKILL.md against the make-skill rules. Returns (errors, warnings); errors block a save."""
    errors, warnings = [], []
    m = re.match(r"---\n(.*?)\n---\n", text, re.S)
    if not m:
        return ["frontmatter missing: file must start with --- name/description ---"], warnings
    fm = m.group(1)
    name = re.search(r"^name:\s*(.+)$", fm, re.M); desc = re.search(r"^description:\s*(.+)$", fm, re.M)
    if not name: errors.append("frontmatter has no name")
    elif name.group(1).strip().strip("\"'") != dirname: errors.append(f"name '{name.group(1).strip()}' must equal the folder name '{dirname}'")
    if not desc or not desc.group(1).strip().strip("\"'"): errors.append("frontmatter has no description")
    elif len(desc.group(1)) > 1024: errors.append(f"description is {len(desc.group(1))} chars; limit 1024")
    lines = text.count("\n") + 1; words = len(text.split())
    if lines > 120: warnings.append(f"{lines} lines; make-skill budget is 120")
    if words > 900: warnings.append(f"{words} words; make-skill budget is ~900")
    return errors, warnings

CONNECT_HEADING = "## Works with"

def add_connection(text, other_name, how):
    """Return (new_text, line) with a bullet appended under CONNECT_HEADING (created at the end if absent).
    The bullet always contains `other_name` in backticks so the reference graph picks it up."""
    how = " ".join(how.split()).strip().rstrip(".") + "."
    if f"`{other_name}`" not in how and f"/{other_name}" not in how:
        how = f"`{other_name}` — {how}"
    line = f"- {how}"
    body = text.rstrip("\n")
    if re.search(rf"^{re.escape(CONNECT_HEADING)}\s*$", body, re.M):
        # append after the last bullet of that section
        parts = re.split(rf"(^{re.escape(CONNECT_HEADING)}\s*$)", body, maxsplit=1, flags=re.M)
        head, heading, rest = parts[0], parts[1], parts[2]
        m = re.search(r"^#{1,6}\s", rest.lstrip("\n"), re.M)  # next heading inside rest?
        if m:
            idx = rest.lstrip("\n").index(m.group(0)); lead = len(rest) - len(rest.lstrip("\n"))
            section, tail = rest[:lead + idx].rstrip("\n"), rest[lead + idx:]
            new = f"{head}{heading}{section}\n{line}\n\n{tail}"
        else:
            new = f"{head}{heading}{rest.rstrip()}\n{line}"
    else:
        new = f"{body}\n\n{CONNECT_HEADING}\n{line}"
    return new.rstrip("\n") + "\n", line

def graph(personal, plugs, synced, instr):
    """Nodes = skills, plugin skills, synced skills, plugins, global files. Edges = textual references between them."""
    nodes, by_name = [], {}
    def add(nid, name, kind, path=None, plugin=None):
        nodes.append({"id": nid, "name": name, "kind": kind, "path": path, "plugin": plugin})
        by_name.setdefault(name, []).append(nid)
    for sk in personal: add(f"skill:{sk['name']}", sk["name"], "skill", str(Path(sk["path"]) / "SKILL.md"))
    for pl in plugs:
        add(f"plugin:{pl['name']}", pl["name"], "plugin")
        for sk in pl["skills"]: add(f"pskill:{pl['name']}/{sk['name']}", sk["name"], "pskill", str(Path(sk["path"]) / "SKILL.md"), pl["name"])
    for sk in synced:
        if sk.get("path"): add(f"synced:{sk['name']}", sk["name"], "synced", str(Path(sk["path"]) / "SKILL.md"))
    for f in instr: add(f"file:{f['name']}", f["name"], "file", str(Path(f["dir"]) / f["file"]))
    PRIO = {"skill": 0, "pskill": 1, "synced": 2, "file": 3, "plugin": 4}
    def resolve(name, exclude):
        c = [n for n in by_name.get(name, []) if n != exclude]
        return sorted(c, key=lambda n: PRIO[n.split(":")[0]])[:1]
    # matching patterns per name
    def pattern(name, kind):
        e = re.escape(name)
        if kind == "file":
            stem = re.escape(name[:-3])
            return re.compile(rf"(?<!\w){e}\b" if "-" not in name else rf"(?<!\w)(?:{e}|{stem})\b")
        if "-" in name:
            return re.compile(rf"(?<![\w.]){e}(?![\w-])")
        return re.compile(rf"(?:(?<![\w])/{e}\b|`{e}`|\b{e} skill\b|\bskill {e}\b|\b{e} plugin\b)", re.I)
    pats = {n["name"]: pattern(n["name"], n["kind"]) for n in nodes}
    edges, seen = [], set()
    def emit(frm, to, typ, ctx=""):
        k = (frm, to)  # one edge per pair; declared/imports/membership are emitted first and win over mentions
        if k in seen or frm == to: return
        seen.add(k); edges.append({"from": frm, "to": to, "type": typ, "ctx": ctx.strip()[:160]})
    for n in nodes:
        if n["kind"] == "pskill": emit(n["id"], f"plugin:{n['plugin']}", "part of")
        if not n["path"]: continue
        try: text = Path(n["path"]).read_text(errors="replace")
        except OSError: continue
        if n["kind"] == "file":
            for imp in re.findall(r"^@(\S+\.md)", text, re.M):
                for t in resolve(imp, n["id"]): emit(n["id"], t, "imports", f"@{imp}")
        # 1) declared dependencies: bullets under "## Works with" (see ~/.claude/SKILL-CONNECTIONS.md)
        for line, name in works_with(text):
            for t in resolve(name, n["id"]):
                if t.startswith("file:") and n["kind"] != "file": continue  # global files are always loaded: not a dependency
                emit(n["id"], t, "works with", line)
        # 2) undeclared mentions anywhere else in the body
        body = re.sub(r"^---\n.*?\n---\n", "", text, count=1, flags=re.S) if n["kind"] != "file" else text
        body = strip_works_with(body)
        for line in body.split("\n"):
            for name, pat in pats.items():
                if name == n["name"] or not pat.search(line): continue
                for t in resolve(name, n["id"]):
                    if t.startswith("plugin:"): continue  # a plugin container is not a skill; membership is drawn via "part of"
                    if t.startswith("file:") or n["kind"] == "file": continue  # global files are always loaded: only their @imports are drawn
                    emit(n["id"], t, "mention", line)
    return {"nodes": nodes, "edges": edges}

def works_with_section(text):
    """Return the body of the '## Works with' section (up to the next heading), or ''."""
    m = re.search(rf"^{re.escape(CONNECT_HEADING)}\s*$\n(.*?)(?=^#{{1,6}}\s|\Z)", text, re.M | re.S)
    return m.group(1) if m else ""

def strip_works_with(text):
    return re.sub(rf"^{re.escape(CONNECT_HEADING)}\s*$\n.*?(?=^#{{1,6}}\s|\Z)", "", text, flags=re.M | re.S)

def works_with(text):
    """Yield (bullet_line, referenced_name) for each bullet in the Works-with section.
    The name is the first backticked token, with any 'plugin/' or 'plugin:' prefix removed."""
    for line in works_with_section(text).splitlines():
        if not line.lstrip().startswith(("-", "*")): continue
        m = re.search(r"`([^`]+)`", line)
        if not m: continue
        name = m.group(1).strip().split("/")[-1].split(":")[-1]
        yield line.strip(), name

def build():
    settings = read_json(CLAUDE / "settings.json", {})
    static = read_json(STATIC_JSON, None)
    if static is None:  # first run without a personal static.json: fall back to the shipped example
        static = read_json(HERE / "static.example.json", {})
    cloud = read_json(ROUTINES_JSON, {"fetched_at": None, "routines": []})
    plug, plug_hooks = plugins(settings)
    personal, synced, instr = personal_skills(), claude_ai_skills(static.get("claude_ai_skills", [])), instructions()
    data = {
        "title": static.get("title", "Claude setup"),
        "built_at": now_iso(),
        "cloud_fetched_at": cloud.get("fetched_at"),
        "routines": cloud.get("routines", []),
        "routine_notes": static.get("routine_notes", {}),
        "local_tasks": scheduled_tasks(static.get("scheduled_task_cache", {})),
        "loops": [],
        "personal_skills": personal,
        "plugins": plug,
        "claude_ai_skills": synced,
        "builtin_skills": static.get("builtin_skills", []),
        "hooks": settings_hooks(settings) + plug_hooks,
        "instructions": instr,
        "graph": graph(personal, plug, synced, instr),
        "issues": skill_issues(),
        "silenced": silenced(),
        "connectors": static.get("connectors", []) + local_mcp(),
        "extra_flags": static.get("extra_flags", []),
        "model": settings.get("model", ""),
    }
    html = TEMPLATE.read_text().replace("/*__DATA__*/{}", json.dumps(data, ensure_ascii=False))
    OUT.write_text(html)
    return data

if __name__ == "__main__":
    if "--cloud" in sys.argv:
        ok, msg = refresh_cloud()
        print(("cloud ok: " if ok else "cloud FAILED: ") + msg)
        if not ok: sys.exit(1)
    d = build()
    print(f"built {OUT} — {len(d['routines'])} routines, {len(d['personal_skills'])} skills, {len(d['plugins'])} plugins, {len(d['hooks'])} hooks")
