"""Compile the pinned, dependency-free Kalliope core without Maven downloads."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def build(source: Path, output: Path) -> Path:
    lock = json.loads((ROOT / "engine-lock.json").read_text(encoding="utf-8"))
    sources = sorted((source / "kalliope-core/src/main/java").rglob("*.java"))
    if not sources:
        raise ValueError("No Kalliope Java sources found")
    classes = output / "classes"
    classes.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["javac", "--release", lock["javaRelease"], "-encoding", "UTF-8",
         "-d", str(classes), *map(str, sources)], check=True, timeout=90,
    )
    # Include the upstream notice in the built JAR, without changing its license.
    (classes / "META-INF").mkdir(exist_ok=True)
    (classes / "META-INF/KALLIOPE-LICENSE").write_text(
        (source / "LICENSE").read_text(encoding="utf-8"), encoding="utf-8",
    )
    jar = output / "kalliope-core.jar"
    subprocess.run(
        ["jar", "--create", "--file", str(jar), "--main-class", lock["mainClass"],
         "-C", str(classes), "."], check=True, timeout=30,
    )
    return jar


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, help="Existing source checkout (local development only)")
    args = parser.parse_args()
    lock = json.loads((ROOT / "engine-lock.json").read_text(encoding="utf-8"))
    output = Path(tempfile.mkdtemp(prefix="verse-check-", dir=os.getenv("RUNNER_TEMP")))
    source = args.source
    if source is None:
        source = output / "source"
        subprocess.run(["git", "init", "--quiet", str(source)], check=True, timeout=30)
        subprocess.run(
            ["git", "-C", str(source), "fetch", "--quiet", "--depth=1",
             lock["repository"], lock["commit"]], check=True, timeout=120,
        )
        subprocess.run(["git", "-C", str(source), "checkout", "--quiet", "--detach", "FETCH_HEAD"],
                       check=True, timeout=30)
        commit = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
        if commit != lock["commit"]:
            raise ValueError("Fetched engine commit did not match engine-lock.json")
    jar = build(source.resolve(), output)
    if os.getenv("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as stream:
            stream.write(f"jar={jar}\n")
    print(jar)


if __name__ == "__main__":
    main()
