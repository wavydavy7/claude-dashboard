"""Tests for build.py / serve.py. Run: python3 -m unittest -v"""
import json, tempfile, unittest
from pathlib import Path

import build, serve


class TmpHome(unittest.TestCase):
    """Point build's paths at a scratch ~/.claude so tests never touch the real one."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); root = Path(self.tmp.name)
        self.claude = root / ".claude"; (self.claude / "skills").mkdir(parents=True)
        self.here = root / "dash"; self.here.mkdir()
        self._saved = {k: getattr(build, k) for k in ("CLAUDE", "HERE", "ISSUES", "DISMISSED", "SILENCED")}
        build.CLAUDE = self.claude; build.HERE = self.here
        build.ISSUES = self.claude / "skill-issues.jsonl"; build.DISMISSED = self.claude / "skill-issues.dismissed.jsonl"
        build.SILENCED = self.here / "silenced.json"
    def tearDown(self):
        for k, v in self._saved.items(): setattr(build, k, v)
        self.tmp.cleanup()
    def skill(self, name, body, desc="d"):
        d = self.claude / "skills" / name; d.mkdir(parents=True, exist_ok=True)
        (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {desc}\n---\n{body}\n"); return d


class Frontmatter(TmpHome):
    def test_parses_name_and_quoted_description(self):
        d = self.skill("alpha", "body", desc='"Does things, use when asked"')
        self.assertEqual(build.frontmatter(d / "SKILL.md"), ("alpha", "Does things, use when asked"))
    def test_missing_frontmatter(self):
        p = self.claude / "x.md"; p.write_text("# no frontmatter")
        self.assertEqual(build.frontmatter(p), (None, None))
    def test_personal_skills_skip_synced(self):
        self.skill("alpha", ""); (self.claude / "skills/synced/b/morning").mkdir(parents=True)
        (self.claude / "skills/synced/b/morning/SKILL.md").write_text("---\nname: morning\ndescription: brief\n---\n")
        self.assertEqual([s["name"] for s in build.personal_skills()], ["alpha"])
        self.assertEqual([s["name"] for s in build.claude_ai_skills([])], ["morning"])


class Graph(TmpHome):
    def edges(self, personal, files=()):
        g = build.graph(personal, [], [], list(files)); return {(e["from"], e["to"], e["type"]) for e in g["edges"]}
    def test_hyphenated_name_matches_anywhere_and_as_invocation(self):
        self.skill("code-davy", "Gate 8: via `/make-skill`. Also see make-skill rules."); self.skill("make-skill", "")
        e = self.edges(build.personal_skills())
        self.assertIn(("skill:code-davy", "skill:make-skill", "mention"), e)
    def test_single_word_name_needs_context(self):
        self.skill("run", ""); self.skill("a", "Then run the tests.")            # plain word: no edge
        self.skill("b", "Use the `run` skill or /run.")                         # backtick / slash: edge
        e = self.edges(build.personal_skills())
        self.assertNotIn(("skill:a", "skill:run", "mention"), e)
        self.assertIn(("skill:b", "skill:run", "mention"), e)
    def test_skill_to_global_file_is_not_an_edge_imports_win(self):
        (self.claude / "CLAUDE.md").write_text("@DEV-PROCESS.md\n# Rules\nSee DEV-PROCESS.md often.\n")
        (self.claude / "DEV-PROCESS.md").write_text("# Dev Process\n")
        self.skill("s", "Sources: `~/.claude/DEV-PROCESS.md`.")
        instr = build.instructions(); e = self.edges(build.personal_skills(), instr)
        self.assertFalse([x for x in e if x[0] == "skill:s" and x[1] == "file:DEV-PROCESS.md"])  # always-loaded: never an edge
        self.assertIn(("file:CLAUDE.md", "file:DEV-PROCESS.md", "imports"), e)
        self.assertNotIn(("file:CLAUDE.md", "file:DEV-PROCESS.md", "mention"), e)  # one edge per pair
    def test_no_self_edges(self):
        self.skill("self-ref", "This is the self-ref skill.")
        self.assertFalse([x for x in self.edges(build.personal_skills()) if x[0] == x[1]])


class Issues(TmpHome):
    def test_read_sorted_and_bad_line_reported(self):
        build.ISSUES.write_text('{"id":"a","ts":"2026-01-01T00:00:00Z","skill":"x","summary":"old"}\nnot json\n{"id":"b","ts":"2026-02-01T00:00:00Z","skill":"y","summary":"new"}\n')
        got = build.skill_issues()
        self.assertEqual([i["id"] for i in got][:2], ["b", "a"]); self.assertTrue(any(i["id"].startswith("bad") for i in got))
    def test_dismiss_moves_line(self):
        build.ISSUES.write_text('{"id":"a","skill":"x","summary":"s"}\n{"id":"b","skill":"y","summary":"t"}\n')
        self.assertTrue(build.dismiss_issue("a")); self.assertFalse(build.dismiss_issue("zzz"))
        self.assertEqual([i["id"] for i in build.skill_issues()], ["b"])
        gone = json.loads(build.DISMISSED.read_text().strip()); self.assertEqual(gone["id"], "a"); self.assertIn("dismissed_at", gone)
    def test_silence_roundtrip(self):
        self.assertEqual(build.set_silenced("k1", True), ["k1"]); self.assertEqual(build.set_silenced("k1", True), ["k1"])
        self.assertEqual(build.set_silenced("k1", False), []); self.assertEqual(build.silenced(), [])


class SkillEndpoint(TmpHome):
    def setUp(self):
        super().setUp(); self._roots = serve.SKILL_ROOTS; self._home = serve.CLAUDE_HOME
        serve.SKILL_ROOTS = [self.claude / "skills"]; serve.CLAUDE_HOME = self.claude.resolve()
        self.d = self.skill("alpha", "# Alpha\n"); (self.d / "notes.md").write_text("ref")
        (self.claude / "settings.json").write_text("{}"); (self.claude / "CLAUDE.md").write_text("# G\n")
    def tearDown(self):
        serve.SKILL_ROOTS = self._roots; serve.CLAUDE_HOME = self._home; super().tearDown()
    def test_reads_skill_and_lists_files(self):
        body, code = serve.skill_payload(str(self.d), None)
        self.assertEqual(code, 200); self.assertEqual(body["file"], "SKILL.md"); self.assertEqual([f["rel"] for f in body["files"]], ["SKILL.md", "notes.md"])
    def test_refuses_outside_roots_and_escapes(self):
        self.assertEqual(serve.skill_payload(str(self.claude / "plugins"), None)[1], 403)
        self.assertEqual(serve.skill_payload("/etc", None)[1], 403)
        self.assertEqual(serve.skill_payload(str(self.d), "../../settings.json")[1], 404)
    def test_claude_home_exposes_only_top_level_md(self):
        body, code = serve.skill_payload(str(self.claude), None)
        self.assertEqual(code, 200); self.assertEqual([f["rel"] for f in body["files"]], ["CLAUDE.md"])
        self.assertEqual(serve.skill_payload(str(self.claude), "settings.json")[1], 404)
        self.assertEqual(serve.skill_payload(str(self.claude), "skills/alpha/SKILL.md")[1], 404)


if __name__ == "__main__":
    unittest.main()


class Validate(unittest.TestCase):
    def test_good_skill_passes(self):
        e, w = build.validate_skill("---\nname: alpha\ndescription: does x, use when y\n---\n# Alpha\n", "alpha")
        self.assertEqual((e, w), ([], []))
    def test_name_must_match_folder(self):
        e, _ = build.validate_skill("---\nname: beta\ndescription: d\n---\n", "alpha"); self.assertTrue(any("must equal the folder" in x for x in e))
    def test_missing_frontmatter_blocks(self):
        e, _ = build.validate_skill("# no frontmatter\n", "alpha"); self.assertTrue(e)
    def test_budget_is_warning_not_error(self):
        e, w = build.validate_skill("---\nname: alpha\ndescription: d\n---\n" + "line\n" * 130, "alpha")
        self.assertEqual(e, []); self.assertTrue(any("lines" in x for x in w))


class Save(TmpHome):
    def setUp(self):
        super().setUp(); self._r = (serve.SKILL_ROOTS, serve.WRITE_ROOTS, serve.CLAUDE_HOME, serve.BACKUPS)
        serve.SKILL_ROOTS = [self.claude / "skills"]; serve.WRITE_ROOTS = [self.claude / "skills"]; serve.CLAUDE_HOME = self.claude.resolve(); serve.BACKUPS = self.here / "backups"
        self.d = self.skill("alpha", "# v1")
        (self.claude / "skills/synced/b/pdf").mkdir(parents=True); (self.claude / "skills/synced/b/pdf/SKILL.md").write_text("---\nname: pdf\ndescription: d\n---\n")
        (self.claude / "CLAUDE.md").write_text("# G\n"); (self.claude / "settings.json").write_text("{}")
        (self.claude / "template.html").write_text("x"); build.TEMPLATE = self.claude / "template.html"; build.OUT = self.here / "dashboard.html"
    def tearDown(self):
        serve.SKILL_ROOTS, serve.WRITE_ROOTS, serve.CLAUDE_HOME, serve.BACKUPS = self._r; super().tearDown()
    def test_saves_with_backup(self):
        new = "---\nname: alpha\ndescription: d\n---\n# v2\n"
        out, code = serve.save_payload(str(self.d), "SKILL.md", new)
        self.assertEqual(code, 200, out); self.assertEqual((self.d / "SKILL.md").read_text(), new)
        self.assertIn("# v1", Path(out["backup"]).read_text())
    def test_rejects_bad_frontmatter(self):
        out, code = serve.save_payload(str(self.d), "SKILL.md", "---\nname: wrong\ndescription: d\n---\n")
        self.assertEqual(code, 422); self.assertIn("# v1", (self.d / "SKILL.md").read_text())
    def test_synced_and_settings_are_read_only(self):
        self.assertEqual(serve.save_payload(str(self.claude / "skills/synced/b/pdf"), "SKILL.md", "---\nname: pdf\ndescription: d\n---\n")[1], 403)
        self.assertEqual(serve.save_payload(str(self.claude), "settings.json", "{}")[1], 403)
        self.assertEqual(serve.save_payload(str(self.d), "../../settings.json", "{}")[1], 400)
    def test_global_md_writable_no_create(self):
        self.assertEqual(serve.save_payload(str(self.claude), "CLAUDE.md", "# G2\n")[1], 200)
        self.assertEqual(serve.save_payload(str(self.claude), "NEW.md", "x")[1], 404)


class Connect(unittest.TestCase):
    base = "---\nname: a\ndescription: d\n---\n# A\n\n## Procedure\n1. step\n"
    def test_creates_section_and_backticks_name(self):
        new, line = build.add_connection(self.base, "precedent", "run it before planning")
        self.assertEqual(line, "- `precedent` — run it before planning.")
        self.assertTrue(new.endswith("## Works with\n- `precedent` — run it before planning.\n"))
    def test_appends_to_existing_section_before_next_heading(self):
        first, _ = build.add_connection(self.base + "\n## Works with\n- `x` — one.\n\n## Pointers\n- p\n", "y", "two")
        self.assertIn("- `x` — one.\n- `y` — two.\n\n## Pointers", first)
    def test_keeps_user_backtick_or_slash(self):
        _, l1 = build.add_connection(self.base, "integrate", "invoke `integrate` when data crosses a boundary")
        _, l2 = build.add_connection(self.base, "integrate", "call /integrate first")
        self.assertEqual(l1, "- invoke `integrate` when data crosses a boundary."); self.assertEqual(l2, "- call /integrate first.")
    def test_idempotent_section_count(self):
        new, _ = build.add_connection(self.base, "x", "one"); new, _ = build.add_connection(new, "y", "two")
        self.assertEqual(new.count("## Works with"), 1)


class WorksWith(TmpHome):
    def test_declared_edge_wins_and_mentions_are_separate(self):
        self.skill("a", "Step: call `b` here.\n\n## Works with\n- `c` — invoke before step 1.\n"); self.skill("b", ""); self.skill("c", "")
        g = build.graph(build.personal_skills(), [], [], []); e = {(x["from"], x["to"]): x["type"] for x in g["edges"]}
        self.assertEqual(e[("skill:a", "skill:c")], "works with"); self.assertEqual(e[("skill:a", "skill:b")], "mention")
    def test_plugin_prefixed_name_resolves(self):
        lines = list(build.works_with("## Works with\n- `env-pod-helper/agent-env-local-dev` — stack.\n- `claude-code-routines:routine-maintenance` — update.\n- no backtick here\n"))
        self.assertEqual([n for _, n in lines], ["agent-env-local-dev", "routine-maintenance"])
    def test_section_bounded_by_next_heading(self):
        self.assertEqual(build.works_with_section("## Works with\n- `x` — a.\n\n## Pointers\n- `y`\n").strip(), "- `x` — a.")
