#!/usr/bin/env python3
"""Verify saved E3 evidence and the portable C0/C1/C2 history without running builds."""
import argparse
import hashlib
import json
import subprocess
import tempfile
from pathlib import Path


def verify(directory):
    directory = directory.resolve()
    manifest = json.loads((directory / "manifest.json").read_text())
    required = {"summary.json", "environment.json", "commands.json", "commits.json", "fixture-history.bundle"}
    if not required <= manifest.keys():
        raise ValueError("Incomplete evidence manifest")
    for name, digest in manifest.items():
        path = (directory / name).resolve()
        if not path.is_relative_to(directory):
            raise ValueError("Manifest path escapes evidence directory")
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError("Evidence hash mismatch: " + name)
    summary = json.loads((directory / "summary.json").read_text())
    if summary["status"] != "PASSED" or not summary["assertions"]:
        raise ValueError("Run did not complete successfully")
    for item in summary["assertions"]:
        if not item["passed"] or item["actual"] != item["expected"]:
            raise ValueError("Failed assertion: " + item["label"])
    commands = json.loads((directory / "commands.json").read_text())
    for cmd in commands:
        if cmd["exit_code"] != 0 or cmd["stdout"] not in manifest or cmd["stderr"] not in manifest:
            raise ValueError("Failed or missing command evidence")
    versions = json.loads((directory / "commits.json").read_text())
    with tempfile.TemporaryDirectory(prefix="e3-verify-") as temp:
        repo = Path(temp) / "fixture"
        def git(*args):
            return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()
        subprocess.run(["git", "clone", "--quiet", str(directory / "fixture-history.bundle"), str(repo)], check=True)
        for version in ("C0", "C1", "C2"):
            if git("rev-parse", version) != versions[version]["commit"]:
                raise ValueError("Bundle commit mismatch: " + version)
            for name, digest in versions[version]["source_sha256"].items():
                data = subprocess.check_output(["git", "-C", str(repo), "show", version + ":" + name])
                if hashlib.sha256(data).hexdigest() != digest:
                    raise ValueError("Bundle source mismatch: " + version + "/" + name)
        for before, after in (("C0", "C1"), ("C1", "C2")):
            if git("rev-parse", after + "^") != versions[before]["commit"]:
                raise ValueError("Versions are not consecutive commits")
        if git("diff", "--name-only", "C1", "C2") != "Makefile":
            raise ValueError("C2 must only change Makefile")
        if sorted(git("diff", "--name-only", "C0", "C1").splitlines()) != ["feature.h", "main.c"]:
            raise ValueError("C1 changes do not match course requirements")
    print(f"E3 evidence verified: {len(manifest)} files, {len(summary['assertions'])} assertions, 3 real commits")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evidence_dir", type=Path)
    verify(parser.parse_args().evidence_dir)
