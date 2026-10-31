# 🪶 Kalliope Verse Check

[![Verify the verse checker](https://github.com/aporkolab/verse-check/actions/workflows/ci.yml/badge.svg)](https://github.com/aporkolab/verse-check/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/wrapper-MIT-8575ff)](LICENSE)

**Your tests pass. Does your hexameter?**

A GitHub Action that scans Hungarian verse using the actual [Kalliope](https://github.com/aporkolab/Kalliope) engine. It checks each line against a metre, writes a readable job summary, and returns a JSON report. It can also check whether your pull request title is a hexameter. That part is entirely optional. Mostly.

```text
Jót s jól! Ebben áll a nagy titok. Ezt ha nem érted,
                                              ✓ hexameter
```

The example is the first line of **A nagy titok** by **Kazinczy Ferenc (1811)**, a public-domain poem. It is also the real-engine smoke test.

## Add it to a workflow

Put UTF-8 plain-text poems in `poems/`. Blank lines separate stanzas. Keep titles, author names and Markdown formatting outside these files.

```yaml
name: Check the metre
on: [push, pull_request]
permissions:
  contents: read
jobs:
  verse:
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@v7
        with:
          persist-credentials: false
      - uses: aporkolab/verse-check@v1
        id: verse
        with:
          files: poems/**/*.txt
          expected-meter: hexameter
          strict: 'true'
```

For a report that never blocks a merge over metre, leave `strict` at its default, `'false'`. A missing file, invalid configuration or engine failure still fails the action.

`v1` is the maintained major-version branch. For production workflows, pin this action to a reviewed full commit SHA or a versioned release tag.

## PR title mode

On `pull_request`, the action reads the title from GitHub's JSON event file. The title never enters a shell command.

```yaml
name: Is this PR a hexameter?
on:
  pull_request:
    types: [opened, synchronize, reopened, edited]
permissions:
  contents: read
jobs:
  title:
    runs-on: ubuntu-24.04
    steps:
      - uses: aporkolab/verse-check@v1
        with:
          files: ''
          check-pr-title: 'true'
```

Title-only mode needs no checkout. Enable `strict: 'true'` only if you really mean to require verse. English titles are not a supported prosody use case: this is a Hungarian engine.

## Inputs

| Input | Default | Meaning |
| :--- | :--- | :--- |
| `files` | `poems/**/*.txt` | One workspace-relative glob per line. Spaces in paths work. Every nonempty pattern must match. Use `''` for title-only mode. |
| `expected-meter` | `hexameter` | Exact Kalliope metre ID or name, case-insensitive. `any` accepts any recognized quantitative metre. Applies to files. |
| `check-pr-title` | `'false'` | Also check the PR title for hexameter. Requires a `pull_request` event. |
| `strict` | `'false'` | Fail the step when any checked line does not match. |

Multiple inputs:

```yaml
with:
  files: |
    poems/**/*.txt
    drafts/poem with spaces.txt
```

## Outputs and reports

| Output | Meaning |
| :--- | :--- |
| `passed` | `'true'` when all checked lines match; otherwise `'false'`. |
| `checked-lines` | Number of analyzed lines, including the optional PR title. |
| `mismatched-lines` | Number of lines without the required metre. |
| `report` | Absolute path to `report.json` in a temporary runner directory. |

The job summary shows scansion and recognized metres for each line. The JSON report includes the source, stanza and line indices, text, scansion, metre IDs and names, expected metre, match result, and pinned engine commit. Reports are written before a strict-mode failure. Upload the JSON with your preferred artifact-upload step if you want to retain it after the job.

## How it works

1. Install Temurin JDK 17.
2. Fetch the exact Kalliope commit in [`engine-lock.json`](engine-lock.json), then verify the checked-out SHA.
3. Compile its original dependency-free `kalliope-core` with `javac --release 17`, preserving the upstream license in the temporary JAR.
4. Send each poem to `KalliopeCli --json -` over stdin. Interpret the engine's actual `meters` results.

There is no reimplementation of syllable counting or metre matching here. No LLM, API key, Maven install, npm dependency or external poem-analysis service is involved. The engine download requires network access; poem analysis runs on the runner.

Supported environment: GitHub-hosted Ubuntu runners, with Python 3.10+, Git and Bash. `setup-java` installs the JDK. Action dependencies and the engine are pinned; Dependabot maintains the action references and the development tools. An engine update is an explicit change to `engine-lock.json`, accompanied by fresh integration results.

## What a mismatch means

Kalliope uses its default pronunciation and ambiguity rules. A line can be valid poetry without matching the requested quantitative metre. Hungarian accentual verse, poetic licenses, spelling variants and multiple possible readings need interpretation. `any` concerns quantitative metres; it does not accept every accentual pattern the engine knows. A green check confirms the configured match, not literary quality.

The wrapper limits each invocation to 50 files, 64 KiB per file and 256 KiB combined. Engine subprocesses have a 20-second timeout and a 256 MiB Java heap limit. File globs cannot escape the workspace through parent paths or symlinks. Only the first 150 lines appear in the job summary; the JSON report is complete.

## Development

```bash
# JDK 17+ and Python 3.10+ required. The action itself has no Python dependencies;
# requirements-dev.txt pins the linter and test runner used by CI.
python3 -m pip install -r requirements-dev.txt
python3 -m ruff check .

python3 scripts/build_engine.py
# The command prints the temporary JAR path. Use that exact path below:
export VERSE_ENGINE_JAR=/path/printed/by/build/kalliope-core.jar
python3 -m pytest -v

VERSE_FILES=examples/hexameter.txt VERSE_STRICT=true python3 scripts/verse_check.py
```

For an existing checkout of the locked Kalliope revision, use `python3 scripts/build_engine.py --source /path/to/Kalliope`. This option is a local-development convenience, not an action input. Without `VERSE_ENGINE_JAR` the integration tests are skipped and only the engine-free unit tests run. CI lints with ruff, exercises the public composite action and runs all tests against the pinned engine. The checked-in JSON fixture was emitted by that engine for `examples/hexameter.txt`.

## Credits and license

The wrapper and workflow are MIT-licensed, © 2026 Porkoláb Ádám.

**Kalliope is a joint work.** Váradi Nagy Pál created the original program and metrical canon around 2004–2006. Porkoláb Ádám built the modern Java implementation and web application. Kalliope keeps its [own copyright, MIT license and corpus notices](https://github.com/aporkolab/Kalliope/blob/fe186ed2bcd046afc359b78c516c7bfafbee7635/LICENSE). This action fetches that project at runtime; its upstream corpus is not relicensed by this repository.

Built by [Porkoláb Ádám](https://github.com/aporkolab). Prefer hitting bugs to scanning verse? [Play Whack-a-Bug](https://aporkolab.github.io/).
