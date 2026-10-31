import importlib.util
import json
import os
from pathlib import Path
import shutil
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


    def test_workspace_path_with_glob_metacharacters_is_literal(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "repo[1]"
            (workspace / "poems").mkdir(parents=True)
            (workspace / "poems/one.txt").write_text(HEXAMETER, encoding="utf-8")
            self.assertEqual(verse_check.collect_files(workspace, "poems/*.txt"),
                             [(workspace / "poems/one.txt").resolve()])

    def test_patterns_cannot_leave_the_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            for pattern in ("../*.txt", "poems/../../*.txt", "/etc/*"):
                with self.subTest(pattern=pattern), self.assertRaisesRegex(ValueError, "inside"):
                    verse_check.collect_files(Path(directory), pattern)

    def test_overlapping_patterns_are_deduplicated_and_sorted(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            (workspace / "poems/sub").mkdir(parents=True)
            for name in ("b.txt", "a.txt", "sub/c.txt"):
                (workspace / "poems" / name).write_text("x", encoding="utf-8")
            found = verse_check.collect_files(workspace, "poems/**/*.txt\n\n  poems/a.txt  \n")
            self.assertEqual([path.relative_to(workspace.resolve()).as_posix() for path in found],
                             ["poems/a.txt", "poems/b.txt", "poems/sub/c.txt"])

    def test_file_count_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            for index in range(verse_check.MAX_FILES + 1):
                (workspace / f"{index}.txt").write_text("x", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "At most"):
                verse_check.collect_files(workspace, "*.txt")

    def test_boolean_inputs_are_exact(self):
        self.assertTrue(verse_check.boolean("true"))
        self.assertFalse(verse_check.boolean("false"))
        for value in ("True", "yes", "1", ""):
            with self.subTest(value=value), self.assertRaises(ValueError):
                verse_check.boolean(value)

    def test_expected_metre_matches_id_or_name_case_insensitively(self):
        fixture = json.loads((ROOT / "tests/fixtures/hexameter.json").read_text(encoding="utf-8"))
        for expected in ("HEXAMETER", "Hexameter", "any"):
            with self.subTest(expected=expected):
                self.assertTrue(verse_check.evaluate(fixture, expected)[0]["passed"])

    def test_any_rejects_lines_without_quantitative_metre(self):
        analysis = {"stanzas": [{"index": 0, "lines": [
            {"index": 0, "text": "Ez próza.", "scansion": "-U-", "meters": []}]}]}
        row = verse_check.evaluate(analysis, "any")[0]
        self.assertFalse(row["passed"])
        self.assertEqual((row["stanza"], row["line"], row["meters"]), (1, 1, []))

    def test_analysis_without_lines_is_an_error(self):
        with self.assertRaisesRegex(ValueError, "no verse lines"):
            verse_check.evaluate({"stanzas": []}, "hexameter")

    def test_cell_neutralizes_markdown_and_newlines(self):
        rendered = verse_check.cell("[x](javascript:1)\n**b**")
        for raw in ("[", "]", "(", ")", "*", "\n"):
            self.assertNotIn(raw, rendered)

    def test_summary_is_truncated_but_counts_every_line(self):
        lines = [{"stanza": 1, "line": index + 1, "scansion": "-", "meters": [],
                  "expected": "hexameter", "passed": False} for index in range(200)]
        report = {"passed": False, "checkedLines": 200, "mismatchedLines": 200,
                  "engineCommit": "b" * 40, "documents": [{"source": "a.txt", "lines": lines},
                                                          {"source": "b.txt", "lines": lines[:5]}]}
        rendered = verse_check.summary(report)
        self.assertEqual(rendered.count("| a.txt |"), verse_check.SUMMARY_LINES)
        self.assertNotIn("| b.txt |", rendered)
        self.assertIn("Summary truncated after 150 lines", rendered)
        self.assertIn("200 lines checked", rendered)


class RunValidationTests(unittest.TestCase):
    """Configuration checks in run() happen before the engine is ever started."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        jar = self.root / "engine.jar"
        jar.write_bytes(b"")
        self.env = {"GITHUB_WORKSPACE": str(self.root), "VERSE_ENGINE_JAR": str(jar),
                    "VERSE_FILES": "*.txt", "RUNNER_TEMP": str(self.root)}

    def run_with(self, **overrides):
        return verse_check.run({**self.env, **overrides})

    def test_rejects_multiline_or_empty_expected_metre(self):
        for value in ("", "   ", "hexa\nmeter", "x" * 121):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "expected-meter"):
                self.run_with(VERSE_EXPECTED_METER=value)

    def test_rejects_oversized_file(self):
        (self.root / "big.txt").write_bytes(b"a" * (verse_check.MAX_FILE_BYTES + 1))
        with self.assertRaisesRegex(ValueError, "64 KiB"):
            self.run_with()

    def test_rejects_oversized_total(self):
        for index in range(5):
            (self.root / f"{index}.txt").write_bytes(b"a" * verse_check.MAX_FILE_BYTES)
        with self.assertRaisesRegex(ValueError, "256 KiB"):
            self.run_with()

    def test_requires_some_input(self):
        with self.assertRaisesRegex(ValueError, "No inputs selected"):
            self.run_with(VERSE_FILES="")

    def test_rejects_event_without_a_title(self):
        event = self.root / "event.json"
        event.write_text(json.dumps({"pull_request": {"title": "  "}}), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "valid pull request title"):
            self.run_with(VERSE_FILES="", VERSE_CHECK_PR_TITLE="true",
                          GITHUB_EVENT_NAME="pull_request", GITHUB_EVENT_PATH=str(event))

    def test_missing_engine_jar_is_an_error(self):
        with self.assertRaises(OSError):
            self.run_with(VERSE_ENGINE_JAR=str(self.root / "missing.jar"))

    @unittest.skipUnless(shutil.which("java"), "Requires a Java runtime")
    def test_engine_failure_reports_the_engine_message(self):
        with self.assertRaisesRegex(ValueError, "Kalliope engine exited with status"):
            verse_check.analyze(Path(self.env["VERSE_ENGINE_JAR"]), HEXAMETER)



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
