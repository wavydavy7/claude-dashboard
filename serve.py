#!/usr/bin/env python3
"""Serve dashboard.html on localhost and handle the Refresh button.

  GET /                      dashboard
  GET /refresh?mode=fast     rebuild from local sources (seconds)
  GET /refresh?mode=full     also re-list cloud routines via `claude -p` (1-3 min)
  GET /status                {"running": bool, "last": {...}}
  GET /skill?path=<dir>[&file=<rel>]   SKILL.md (or another file) + file list for a skill folder
  GET /issue/dismiss?id=<id>           move a logged skill issue to the dismissed file, rebuild
  GET /flag/silence?key=<k>            hide a computed Needs-attention bullet (silenced.json), rebuild
  GET /flag/unsilence?key=<k>          show it again
"""
import json, sys, threading, time, urllib.parse, webbrowser
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

import build

HERE = Path(__file__).resolve().parent
state = {"running": False, "last": None}
# Only these roots may be read through /skill. Everything else is refused.
SKILL_ROOTS = [Path.home() / ".claude/skills", Path.home() / ".claude/plugins/cache", Path.home() / ".claude/scheduled-tasks"]
MAX_FILE = 400_000

CLAUDE_HOME = (Path.home() / ".claude").resolve()

def skill_payload(dir_s, rel):
    d = Path(dir_s).expanduser().resolve()
    instructions_mode = d == CLAUDE_HOME  # ~/.claude itself: expose ONLY its top-level *.md (CLAUDE.md and the files it @imports)
    if not instructions_mode and not any(d == r or r in d.parents for r in (x.resolve() for x in SKILL_ROOTS)):
        return {"error": "path outside allowed skill roots"}, 403
    if not d.is_dir():
        return {"error": "not a directory"}, 404
    files = []
    candidates = sorted(d.glob("*.md")) if instructions_mode else sorted(d.rglob("*"))
    for f in candidates:
        if f.is_file() and not any(part.startswith(".") for part in f.relative_to(d).parts):
            files.append({"rel": str(f.relative_to(d)), "bytes": f.stat().st_size})
        if len(files) >= 300: break
    target = (d / (rel or ("CLAUDE.md" if instructions_mode else "SKILL.md"))).resolve()
    if d not in target.parents or not target.is_file() or (instructions_mode and (target.parent != d or target.suffix != ".md")):
        return {"error": f"file not found: {rel}"}, 404
    if target.stat().st_size > MAX_FILE:
        content, truncated = target.read_text(errors="replace")[:MAX_FILE], True
    else:
        content, truncated = target.read_text(errors="replace"), False
    return {"dir": str(d), "file": str(target.relative_to(d)), "files": files, "content": content, "truncated": truncated}, 200
lock = threading.Lock()

def do_refresh(mode):
    t0 = time.time(); msgs = []; ok = True
    try:
        if mode == "full":
            cok, cmsg = build.refresh_cloud(); msgs.append(cmsg); ok = ok and cok
        d = build.build(); msgs.append(f"rebuilt: {len(d['routines'])} routines, {len(d['personal_skills'])+sum(len(p['skills']) for p in d['plugins'])} skills")
    except Exception as ex:  # noqa: BLE001 - report to the UI, never hang the button
        ok = False; msgs.append(f"error: {ex!r}")
    with lock:
        state.update(running=False, last={"ok": ok, "mode": mode, "seconds": round(time.time()-t0, 1), "message": "; ".join(msgs), "at": build.now_iso()})

class H(SimpleHTTPRequestHandler):
    def __init__(self, *a, **k): super().__init__(*a, directory=str(HERE), **k)
    def log_message(self, fmt, *a):
        if "/status" not in str(a[0] if a else ""): sys.stderr.write("%s %s\n" % (time.strftime("%H:%M:%S"), fmt % a))
    def send_json(self, obj, code=200):
        b = json.dumps(obj).encode(); self.send_response(code)
        self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(b))); self.send_header("Cache-Control", "no-store"); self.end_headers(); self.wfile.write(b)
    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        if u.path == "/": self.path = "/dashboard.html"
        if u.path == "/status":
            with lock: return self.send_json(state)
        if u.path in ("/flag/silence", "/flag/unsilence"):
            key = urllib.parse.parse_qs(u.query).get("key", [""])[0]
            if not key: return self.send_json({"ok": False, "error": "missing key"}, 400)
            keys = build.set_silenced(key, u.path.endswith("/silence")); build.build()
            return self.send_json({"ok": True, "silenced": keys})
        if u.path == "/issue/dismiss":
            iid = urllib.parse.parse_qs(u.query).get("id", [""])[0]
            ok = build.dismiss_issue(iid)
            if ok: build.build()
            return self.send_json({"ok": ok, "id": iid}, 200 if ok else 404)
        if u.path == "/skill":
            q = urllib.parse.parse_qs(u.query)
            body, code = skill_payload(q.get("path", [""])[0], q.get("file", [None])[0])
            return self.send_json(body, code)
        if u.path == "/refresh":
            mode = urllib.parse.parse_qs(u.query).get("mode", ["fast"])[0]
            with lock:
                if state["running"]: return self.send_json({"started": False, "reason": "already running"}, 409)
                state["running"] = True
            threading.Thread(target=do_refresh, args=(mode,), daemon=True).start()
            return self.send_json({"started": True, "mode": mode})
        return super().do_GET()
    def end_headers(self):
        if self.path.endswith(".html"): self.send_header("Cache-Control", "no-store")
        super().end_headers()

if __name__ == "__main__":
    PORT = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 8765
    if not (HERE / "dashboard.html").exists(): build.build()
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), H)
    url = f"http://localhost:{PORT}/"
    print(f"Claude dashboard at {url}  (Ctrl-C to stop)")
    if "--no-open" not in sys.argv: webbrowser.open(url)
    try: srv.serve_forever()
    except KeyboardInterrupt: pass
