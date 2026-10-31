import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("verse_check", ROOT / "scripts/verse_check.py")
verse_check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verse_check)
HEXAMETER = "Jót s jól! Ebben áll a nagy titok. Ezt ha nem érted,"


class BoundaryTests(unittest.TestCase):
    def test_real_engine_fixture_keeps_exact_hexameter_match(self):
        fixture = json.loads((ROOT / "tests/fixtures/hexameter.json").read_text(encoding="utf-8"))
        result = verse_check.evaluate(fixture, "hexameter")
        self.assertEqual(len(result), 1)
        self.assertTrue(result[0]["passed"])
        self.assertEqual(result[0]["text"], HEXAMETER)

    def test_wrong_expected_metre_does_not_match_fixture(self):
        fixture = json.loads((ROOT / "tests/fixtures/hexameter.json").read_text(encoding="utf-8"))
        self.assertFalse(verse_check.evaluate(fixture, "pentameter")[0]["passed"])

    def test_symlink_cannot_read_outside_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            workspace.mkdir()
            secret = root / "outside.txt"
            secret.write_text("outside", encoding="utf-8")
            (workspace / "poem.txt").symlink_to(secret)
            with self.assertRaisesRegex(ValueError, "outside"):
                verse_check.collect_files(workspace, "*.txt")

    def test_missing_pattern_cannot_pass_silently(self):
        with tempfile.TemporaryDirectory() as directory, \
                self.assertRaisesRegex(ValueError, "matched no files"):
            verse_check.collect_files(Path(directory), "missing/*.txt")

    def test_summary_escapes_user_controlled_markup(self):
        report = {"passed": False, "checkedLines": 1, "mismatchedLines": 1,
                  "engineCommit": "a" * 40, "documents": [{"source": "<img src=x>|bad",
                  "lines": [{"stanza": 1, "line": 1, "scansion": "<script>", "meters": [],
                             "expected": "x|y", "passed": False}]}]}
        rendered = verse_check.summary(report)
        self.assertNotIn("<img", rendered)
        self.assertNotIn("<script>", rendered)
        self.assertIn("&#124;", rendered)


@unittest.skipUnless(os.getenv("VERSE_ENGINE_JAR"), "Set VERSE_ENGINE_JAR for real-engine integration")
class EngineIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="verse check test ")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.env = dict(os.environ, GITHUB_WORKSPACE=str(self.root),
                        GITHUB_OUTPUT=str(self.root / "outputs"),
                        GITHUB_STEP_SUMMARY=str(self.root / "summary"),
                        VERSE_FILES="poem with spaces.txt", VERSE_STRICT="true",
                        VERSE_CHECK_PR_TITLE="false", VERSE_EXPECTED_METER="hexameter")
        (self.root / "poem with spaces.txt").write_text(HEXAMETER, encoding="utf-8")

    def invoke(self):
        return subprocess.run([sys.executable, str(ROOT / "scripts/verse_check.py")],
                              env=self.env, capture_output=True, text=True, timeout=30)

    def outputs(self):
        return dict(line.split("=", 1) for line in (self.root / "outputs").read_text().splitlines())

    def test_real_engine_passes_and_reports_a_file_with_spaces(self):
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        outputs = self.outputs()
        self.assertEqual(outputs["passed"], "true")
        report = json.loads(Path(outputs["report"]).read_text())
        self.assertEqual(report["checkedLines"], 1)
        self.assertEqual(report["documents"][0]["lines"][0]["meters"][0]["name"], "hexameter")
        self.assertIn("Metre matched", (self.root / "summary").read_text())

    def test_prose_fails_strict_but_still_writes_report(self):
        (self.root / "poem with spaces.txt").write_text("A tesztek most már sikeresen lefutnak.")
        result = self.invoke()
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.outputs()["passed"], "false")
        self.assertEqual(self.outputs()["mismatched-lines"], "1")

    def test_report_mode_does_not_fail_for_prose(self):
        (self.root / "poem with spaces.txt").write_text("Ez próza.")
        self.env["VERSE_STRICT"] = "false"
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.outputs()["passed"], "false")

    def test_pr_title_only_uses_actual_engine(self):
        event = self.root / "event.json"
        event.write_text(json.dumps({"pull_request": {"title": HEXAMETER}}))
        self.env.update(VERSE_FILES="", VERSE_CHECK_PR_TITLE="true",
                        GITHUB_EVENT_NAME="pull_request", GITHUB_EVENT_PATH=str(event))
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.outputs()["passed"], "true")

    def test_untrusted_pr_title_never_executes_as_shell(self):
        marker = self.root / "executed"
        title = f'$(touch "{marker}") ; echo oops'
        event = self.root / "event.json"
        event.write_text(json.dumps({"pull_request": {"title": title}}))
        self.env.update(VERSE_FILES="", VERSE_CHECK_PR_TITLE="true", VERSE_STRICT="false",
                        GITHUB_EVENT_NAME="pull_request", GITHUB_EVENT_PATH=str(event))
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(marker.exists())

    def test_empty_poem_is_error_even_in_report_mode(self):
        (self.root / "poem with spaces.txt").write_text(" \n\n")
        self.env["VERSE_STRICT"] = "false"
        result = self.invoke()
        self.assertEqual(result.returncode, 1)
        self.assertIn("no verse lines", result.stderr)

    def test_pr_title_mode_rejects_wrong_event(self):
        self.env.update(VERSE_CHECK_PR_TITLE="true", GITHUB_EVENT_NAME="push")
        result = self.invoke()
        self.assertEqual(result.returncode, 1)
        self.assertIn("pull_request event", result.stderr)


if __name__ == "__main__":
    unittest.main()
