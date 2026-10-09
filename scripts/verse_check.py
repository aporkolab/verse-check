"""Run the real Kalliope CLI and report exact metre matches to GitHub Actions."""

import glob
import html
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
MAX_FILES = 50
MAX_FILE_BYTES = 64 * 1024
MAX_TOTAL_BYTES = 256 * 1024


def boolean(value: str) -> bool:
    if value not in ("true", "false"):
        raise ValueError("Boolean inputs must be 'true' or 'false'")
    return value == "true"


def collect_files(workspace: Path, patterns: str) -> list[Path]:
    """Expand globs without invoking a shell; reject symlinks outside the workspace."""
    workspace = workspace.resolve()
    found = set()
    for pattern in patterns.splitlines():
        pattern = pattern.strip()
        if not pattern:
            continue
        if Path(pattern).is_absolute() or ".." in Path(pattern).parts:
            raise ValueError("File patterns must stay inside the workspace")
        matches = []
        for candidate in glob.iglob(str(workspace / pattern), recursive=True):
            path = Path(candidate)
            if not path.is_file():
                continue
            path = path.resolve()
            if not path.is_relative_to(workspace):
                raise ValueError("A matched file points outside the workspace")
            matches.append(path)
            found.add(path)
            if len(found) > MAX_FILES:
                raise ValueError(f"At most {MAX_FILES} files can be analyzed per invocation")
        if not matches:
            raise ValueError(f"File pattern matched no files: {pattern!r}")
    return sorted(found)


def analyze(jar: Path, poem: str) -> dict:
    result = subprocess.run(
        ["java", "-Xmx256m", "-Dfile.encoding=UTF-8", "-jar", str(jar), "--json", "-"],
        input=poem, text=True, encoding="utf-8", capture_output=True, timeout=20,
        check=True,
    )
    data = json.loads(result.stdout)
    if not isinstance(data, dict) or not isinstance(data.get("stanzas"), list):
        raise ValueError("The engine returned an unexpected JSON structure")
    return data


def evaluate(analysis: dict, expected: str) -> list[dict]:
    rows = []
    for stanza in analysis["stanzas"]:
        for line in stanza["lines"]:
            matches = [entry["meter"] for entry in line["meters"]]
            passed = bool(matches) if expected == "any" else any(
                expected.casefold() in (meter["name"].casefold(), meter["id"].casefold())
                for meter in matches
            )
            rows.append({
                "stanza": stanza["index"] + 1,
                "line": line["index"] + 1,
                "text": line["text"],
                "scansion": line["scansion"],
                "meters": [{"id": meter["id"], "name": meter["name"]} for meter in matches],
                "expected": expected,
                "passed": passed,
            })
    if not rows:
        raise ValueError("A selected input contains no verse lines")
    return rows


def cell(value: object) -> str:
    escaped = html.escape(str(value), quote=True).replace("\n", " ").replace("\r", " ")
    # Numeric entities keep filenames and input labels from becoming Markdown links.
    return escaped.translate({ord(char): f"&#{ord(char)};" for char in "|`[]()!*_\\"})


def summary(report: dict) -> str:
    passed = report["passed"]
    parts = ["## 🪶 Kalliope Verse Check", "",
             f"**{'Metre matched' if passed else 'Metre mismatch'}** · "
             f"{report['checkedLines']} lines checked · {report['mismatchedLines']} mismatches", "",
             "| Input | Stanza / line | Scansion | Recognized metre | Result |",
             "| :--- | :--- | :--- | :--- | :--- |"]
    shown = 0
    for document in report["documents"]:
        for line in document["lines"]:
            if shown >= 150:
                continue
            names = ", ".join(meter["name"] for meter in line["meters"]) or "No quantitative match"
            parts.append(f"| {cell(document['source'])} | {line['stanza']} / {line['line']} "
                         f"| <code>{cell(line['scansion'])}</code> | {cell(names)} "
                         f"| {'✓' if line['passed'] else '×'} {cell(line['expected'])} |")
            shown += 1
    if report["checkedLines"] > shown:
        parts += ["", "Summary truncated after 150 lines. The JSON report contains every result."]
    parts += ["", "Recognition describes metre under Kalliope’s default pronunciation rules; "
              "it is not a verdict on literary quality. Accentual Hungarian verse can correctly "
              "have no quantitative match.", "",
              f"Engine: [Kalliope `{report['engineCommit'][:12]}`]"
              f"(https://github.com/aporkolab/Kalliope/tree/{report['engineCommit']}). "
              "Original program and canon: Váradi Nagy Pál. Modern implementation: Porkoláb Ádám.", ""]
    return "\n".join(parts)


def run(env: dict) -> dict:
    strict = boolean(env.get("VERSE_STRICT", "false"))
    check_title = boolean(env.get("VERSE_CHECK_PR_TITLE", "false"))
    expected = env.get("VERSE_EXPECTED_METER", "hexameter").strip()
    if not expected or len(expected) > 120 or any(ord(char) < 32 for char in expected):
        raise ValueError("expected-meter must be a nonempty, single-line metre ID or name")
    jar = Path(env["VERSE_ENGINE_JAR"]).resolve(strict=True)
    workspace = Path(env.get("GITHUB_WORKSPACE", os.getcwd())).resolve()
    paths = collect_files(workspace, env.get("VERSE_FILES", "poems/**/*.txt"))
    sources = []
    total_bytes = 0
    for path in paths:
        size = path.stat().st_size
        if size > MAX_FILE_BYTES:
            raise ValueError("Each verse file must be at most 64 KiB")
        total_bytes += size
        if total_bytes > MAX_TOTAL_BYTES:
            raise ValueError("Combined verse files must be at most 256 KiB")
        sources.append((str(path.relative_to(workspace)), path.read_text(encoding="utf-8-sig"), expected))
    if check_title:
        event_path = env.get("GITHUB_EVENT_PATH")
        if not event_path or env.get("GITHUB_EVENT_NAME") != "pull_request":
            raise ValueError("check-pr-title requires a pull_request event")
        event = json.loads(Path(event_path).read_text(encoding="utf-8"))
        title = event.get("pull_request", {}).get("title")
        if not isinstance(title, str) or not title.strip() or len(title) > 1000:
            raise ValueError("The event does not contain a valid pull request title")
        sources.append(("Pull request title", title, "hexameter"))
    if not sources:
        raise ValueError("No inputs selected: provide files or enable check-pr-title")
    documents = [{"source": source, "lines": evaluate(analyze(jar, poem), meter)}
                 for source, poem, meter in sources]
    lines = [line for document in documents for line in document["lines"]]
    mismatched = sum(not line["passed"] for line in lines)
    lock = json.loads((ROOT / "engine-lock.json").read_text(encoding="utf-8"))
    report = {"schemaVersion": 1, "engineCommit": lock["commit"],
              "passed": mismatched == 0, "checkedLines": len(lines),
              "mismatchedLines": mismatched, "documents": documents}
    output_dir = Path(tempfile.mkdtemp(prefix="verse-check-report-", dir=env.get("RUNNER_TEMP")))
    output = output_dir / "report.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if env.get("GITHUB_STEP_SUMMARY"):
        with open(env["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as stream:
            stream.write(summary(report))
    if env.get("GITHUB_OUTPUT"):
        with open(env["GITHUB_OUTPUT"], "a", encoding="utf-8") as stream:
            stream.write(f"passed={str(report['passed']).lower()}\nchecked-lines={len(lines)}\n"
                         f"mismatched-lines={mismatched}\nreport={output}\n")
    print(f"Verse Check: {len(lines)} lines, {mismatched} mismatches. Report: {output}")
    if strict and mismatched:
        raise ValueError(f"Strict metre check failed: {mismatched} mismatched line(s)")
    return report


def main():
    try:
        run(dict(os.environ))
    except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError) as error:
        # JSON quoting keeps user-controlled strings on a single ordinary log line.
        print("Verse Check failed: " + json.dumps(str(error), ensure_ascii=False), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
