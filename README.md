# Claude dashboard

One page showing every Claude Code automation you have: cloud routines, local
scheduled tasks, loops, skills, plugins, hooks, instruction files, connectors.

Local, single-user, Python stdlib only. Nothing leaves your machine except the
optional cloud refresh, which runs your own `claude` CLI.

## Setup

```bash
git clone https://github.com/wavydavy7/claude-dashboard ~/claude-dashboard
cd ~/claude-dashboard
cp static.example.json static.json   # then edit: title, connectors, notes
python3 serve.py
```

Opens http://localhost:8765 in your browser. Keep the terminal open; Ctrl-C stops it.
Paths below assume `~/claude-dashboard`; any folder works.

Requires Python 3.9+ and, for the cloud refresh only, the `claude` CLI logged in.

## Refresh buttons

- **Refresh** — re-reads `~/.claude` (skills, synced claude.ai skills, plugins,
  hooks, scheduled tasks, instruction files, local MCP servers). Seconds.
- **Refresh + cloud** — also re-lists cloud routines by running
  `claude -p` headlessly with `cloud-prompt.txt`. Uses your existing Claude Code
  login in-process; nothing is copied. Takes 1–3 minutes and costs one short
  Sonnet session. Result is cached in `routines.json`.

Without the helper running, `dashboard.html` still opens as a static file; the
buttons then just tell you to start the helper.

## Reading a skill

Click any skill card (yours, a synced claude.ai skill, a plugin skill inside a
plugin's expandable list) or a scheduled-task card. A drawer opens with the
rendered `SKILL.md` and a file list for that folder; click any file to view it.
Esc or the × closes it. Global instruction files (`~/.claude/CLAUDE.md` and
the files it @imports) open the same way. The helper serves files through
`/skill`, which only reads under `~/.claude/skills`, `~/.claude/plugins/cache`,
`~/.claude/scheduled-tasks`, plus the top-level `*.md` files in `~/.claude`
itself. Nothing else in `~/.claude` (settings, credentials, projects) is reachable.

## Editing a skill

In the drawer, **✎ Edit** turns the file into a text editor; **Save** (or ⌘S)
writes it back through the helper. Writable: your own skills under
`~/.claude/skills`, scheduled tasks, and the top-level `~/.claude/*.md` files.
Plugin-cache and synced claude.ai skills are read-only, because a plugin update
overwrites them. Each save keeps the previous version in `backups/` (last 20 per
file) and, for `SKILL.md`, checks the frontmatter: name must equal the folder,
description required and ≤1024 chars; line and word budgets are warnings.
Creating or deleting files is not supported here.

## Connect skills (drag to link)

**⇄ Connect skills** in the header opens a canvas of your skills, global files,
and plugin skills with existing references drawn faintly. Drag from a node's dot
onto another node and a dialog asks how the source should use the target, in one
sentence Claude can act on (when to invoke it, what to do with the result).
Choose a direction, or **Both** to write a sentence into each file. The sentence
is appended as a bullet under `## Works with` in the source file, with the
target's name in backticks so the Flow graph registers the edge. Writes go
through the same path as the editor: backup, make-skill validation, rebuild.
Plugin-cache and synced skills can be targets but are never modified.

## Flow tab

The drawer has a second tab, **Flow**, showing how the item connects to the
rest of your setup: what references it (left), the item (center), what it
references (right). Below the graph, each edge shows the exact line that made
the connection. Click any node to walk to it.

Edges are found by scanning each SKILL.md and global file for other items'
names. Hyphenated names match anywhere; single-word names (`run`, `loop`,
`handoff`) only count as `/name`, `` `name` ``, or "name skill" to avoid hits
on ordinary words. CLAUDE.md `@imports` and plugin membership are dashed.
Heuristic, not semantic: it catches mentions, not runtime calls.

## Silencing Needs-attention bullets

Every bullet has a ×. Computed flags (paused routine, stale task, connector
notes, `extra_flags`) are silenced by a stable key stored in `silenced.json`;
a "N silenced" fold-out under the list lets you restore any of them. Keys for
run-based flags include the run timestamp, so a *new* failure or a new missed
period resurfaces on its own. Logged skill issues are deleted rather than
silenced (see below).

## Logged skill issues

The `skill-issues` skill (`~/.claude/skills/skill-issues/SKILL.md`) appends one JSON
line to `~/.claude/skill-issues.jsonl` whenever a skill's own instructions fail
mid-run. A line in `~/.claude/CLAUDE.md` makes that always-on. Each logged issue
appears at the top of **Needs attention** with the skill name (click to open it)
and a × button. Deleting moves the line to `~/.claude/skill-issues.dismissed.jsonl`
and rebuilds, so nothing is lost.

## Files

| file | purpose |
|---|---|
| `serve.py` | local HTTP helper: serves the page, handles `/refresh`, `/status`, `/skill`, `/issue/dismiss`, `/flag/silence`, `/flag/unsilence`, `POST /skill/save`, `POST /skill/connect` |
| `build.py` | collects data and injects it into `template.html` → `dashboard.html` |
| `template.html` | the page (CSS + render code); `/*__DATA__*/{}` is the injection point |
| `cloud-prompt.txt` | the prompt `claude -p` runs to list routines |
| `static.json` | hand-maintained, git-ignored: title, cloud model, connectors, per-routine notes, scheduled-task run cache, extra flags. Start from `static.example.json` |
| `test_build.py` | unit tests for the builder, graph matcher, issue/silence state, and `/skill` path guards |
| `routines.json` | cache written by the cloud refresh |
| `silenced.json` | keys of Needs-attention bullets you have silenced |
| `backups/` | git-ignored; previous versions of files saved from the editor |
| `dashboard.html` | generated output; do not edit by hand |

## Command line

```bash
python3 build.py           # fast rebuild
python3 build.py --cloud   # rebuild including cloud routines
python3 -m unittest -v     # tests (stdlib unittest, no network, scratch ~/.claude)
```

The cloud refresh model is `cloud_model` in static.json (default `claude-sonnet-5`).

## Known gaps

- Scheduled-task run history (last/next run) is not stored on disk where the
  script can read it; it comes from `static.json` → `scheduled_task_cache`.
  Ask Claude to refresh it, or edit the JSON.
- Pause / Resume / Run buttons copy a paste-ready instruction and open
  claude.ai; they do not call the API directly.
- claude.ai connector list is hand-maintained in `static.json`.

## License

MIT. See `LICENSE`.
