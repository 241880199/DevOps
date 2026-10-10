#!/usr/bin/env python3
"""Run A-group E3 baselines on Linux, keeping all edits inside fresh work copies."""
import argparse
import hashlib
import json
import platform
import re
import shutil
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Lab:
    def __init__(self, directory):
        self.directory = directory
        self.commands = []
        self.assertions = []
        (directory / "logs").mkdir()

    def run(self, argv, cwd, label=None):
        index = len(self.commands) + 1
        label = label or argv[0].replace("/", "_")
        prefix = f"{index:03d}-{label}"
        start = datetime.now(timezone.utc).isoformat()
        try:
            process = subprocess.run(argv, cwd=cwd, capture_output=True, text=True,
                                     errors="replace", timeout=120)
            code, stdout, stderr = process.returncode, process.stdout, process.stderr
        except subprocess.TimeoutExpired as error:
            code = 124
            stdout = error.stdout or b""
            stderr = error.stderr or b""
            stdout = stdout.decode(errors="replace") if isinstance(stdout, bytes) else stdout
            stderr = stderr.decode(errors="replace") if isinstance(stderr, bytes) else stderr
            stderr += "\nrunner timeout after 120 seconds\n"
        out = self.directory / "logs" / (prefix + ".stdout.log")
        err = self.directory / "logs" / (prefix + ".stderr.log")
        out.write_text(stdout, encoding="utf-8")
        err.write_text(stderr, encoding="utf-8")
        self.commands.append({"argv": argv, "cwd": str(cwd), "started_at": start,
                              "exit_code": code,
                              "stdout": out.relative_to(self.directory).as_posix(),
                              "stderr": err.relative_to(self.directory).as_posix()})
        dump(self.directory / "commands.json", self.commands)
        if code != 0:
            raise RuntimeError(f"Command failed ({code}): {argv}; see logs/{prefix}.*.log")
        return stdout

    def expect(self, label, actual, expected):
        passed = actual == expected
        self.assertions.append({"label": label, "expected": expected, "actual": actual,
                                "passed": passed})
        print(f"{'PASS' if passed else 'FAIL'} {label}: {actual}", flush=True)
        if not passed:
            raise AssertionError(f"{label}: expected {expected!r}, got {actual!r}")

    def build(self, cwd, label, clean=False):
        if clean:
            self.run(["make", "clean"], cwd, label + "-clean")
        self.run(["make"], cwd, label + "-make")
        return self.run(["./app"], cwd, label + "-app").strip()


def edit(path, text):
    # Also works on filesystems with coarse timestamp precision; never set future times.
    time.sleep(1.1)
    path.write_text(text, encoding="utf-8")


def md_rd(lab):
    work = lab.directory / "md-rd"
    shutil.copytree(ROOT / "fixtures/md-rd", work)
    lab.expect("MD/RD initial build", lab.build(work, "md-initial", clean=True), "1")
    lab.run(["make", "-pn"], work, "md-make-database")
    trace = work / "trace"
    lab.run(["strace", "-ff", "-o", str(trace), "-e", "trace=%file,%process", "make", "-B"],
            work, "md-raw-strace")
    raw = "\n".join(p.read_text(errors="replace") for p in work.glob("trace.*"))
    lab.expect("Linux trace records config.h open", bool(re.search(r'open(?:at)?\(.*"(?:[^"\n]*/)?config\.h"', raw)), True)
    edit(work / "config.h", "#define VALUE 2\n")
    before = (work / "main.o").stat().st_mtime_ns
    lab.expect("MD incremental retains old value", lab.build(work, "md-header-incremental"), "1")
    lab.expect("MD did not recompile main.o", (work / "main.o").stat().st_mtime_ns, before)
    lab.expect("MD clean uses new value", lab.build(work, "md-header-clean", clean=True), "2")
    before = (work / "main.o").stat().st_mtime_ns
    edit(work / "unused.h", "/* changed but still unused */\n")
    lab.expect("RD output unchanged", lab.build(work, "rd-change"), "2")
    lab.expect("RD triggers extra compilation", (work / "main.o").stat().st_mtime_ns > before, True)


def install_snapshot(version, repo):
    # Write changed files only: C2 must preserve C1 source and object timestamps.
    for source in sorted((ROOT / "fixtures/commits" / version).iterdir()):
        target = repo / source.name
        if not target.exists() or target.read_bytes() != source.read_bytes():
            target.write_bytes(source.read_bytes())


def commits(lab, student):
    repo = lab.directory / "fixture-repo"
    repo.mkdir()
    lab.run(["git", "init", "-b", "main"], repo, "fixture-init")
    lab.run(["git", "config", "--local", "user.name", student], repo)
    lab.run(["git", "config", "--local", "user.email", student + "@localhost"], repo)
    lab.run(["git", "config", "--local", "core.autocrlf", "false"], repo)
    (repo / ".gitignore").write_text("*.o\napp\n", encoding="utf-8")
    versions = {}
    oracle = json.loads((ROOT / "fixtures/commits/oracle.json").read_text())
    for version in ("C0", "C1", "C2"):
        if version != "C0":
            time.sleep(1.1)
        install_snapshot(version, repo)
        lab.run(["git", "add", "."], repo)
        lab.run(["git", "commit", "-m", f"E3 {version} course baseline"], repo)
        lab.run(["git", "tag", version], repo)
        sha = lab.run(["git", "rev-parse", "HEAD"], repo).strip()
        parents = lab.run(["git", "rev-list", "--parents", "-n", "1", "HEAD"], repo).split()[1:]
        versions[version] = {"commit": sha, "parents": parents,
                             "oracle": oracle["versions"][version],
                             "source_sha256": {p.name: sha256(p) for p in repo.iterdir()
                                               if p.is_file() and (p.suffix in (".c", ".h") or p.name == "Makefile")}}
        dump(lab.directory / "commits.json", versions)
        command = lab.run(["make", "-n", "-B", "main.o"], repo, version + "-command")
        versions[version]["compile_command"] = command.strip()
        if version == "C0":
            lab.expect("C0 clean", lab.build(repo, "C0", clean=True), "10")
        elif version == "C1":
            lab.expect("C1 changed paths", sorted(lab.run(["git", "diff", "--name-only", "C0", "C1"], repo).splitlines()), ["feature.h", "main.c"])
            lab.expect("C1 clean", lab.build(repo, "C1", clean=True), "12")
            edit(repo / "feature.h", "#define FEATURE 3\n")
            lab.expect("C1 MD incremental", lab.build(repo, "C1-header-incremental"), "12")
            lab.expect("C1 MD clean", lab.build(repo, "C1-header-clean", clean=True), "13")
            edit(repo / "feature.h", "#define FEATURE 2\n")
            lab.expect("C1 restored for C2", lab.build(repo, "C1-restored", clean=True), "12")
        else:
            lab.expect("C2 changed paths", lab.run(["git", "diff", "--name-only", "C1", "C2"], repo).strip(), "Makefile")
            lab.expect("C2 records MODE=7 command", "-DMODE=7" in command, True)
            lab.expect("C2 incremental keeps C1 products", lab.build(repo, "C2-incremental"), "12")
            lab.expect("C2 clean applies command", lab.build(repo, "C2-clean", clean=True), "19")
    for previous, current in (("C0", "C1"), ("C1", "C2")):
        lab.expect(current + " immediate parent", versions[current]["parents"], [versions[previous]["commit"]])
    lab.expect("Fixture working tree clean", lab.run(["git", "status", "--porcelain"], repo).strip(), "")
    dump(lab.directory / "commits.json", versions)
    lab.run(["git", "bundle", "create", str(lab.directory / "fixture-history.bundle"), "--all"], repo, "history-bundle")
    lab.run(["git", "bundle", "verify", str(lab.directory / "fixture-history.bundle")], repo, "history-bundle-verify")


def environment(student):
    def read(argv):
        p = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True, timeout=20)
        return p.stdout.strip() if p.returncode == 0 else None
    tools = {tool: read([tool, "--version"]).splitlines()[0]
             for tool in ("git", "make", "cc", "python3", "strace")}
    return {"student_id": student, "group": "A03", "time": datetime.now(timezone.utc).isoformat(),
            "os": platform.platform(), "uname": list(platform.uname()), "tools": tools,
            "source_commit": read(["git", "rev-parse", "HEAD"]),
            "source_status": read(["git", "status", "--porcelain"]),
            "fixture_sha256": {p.relative_to(ROOT).as_posix(): sha256(p)
                               for folder in (ROOT / "fixtures/md-rd", ROOT / "fixtures/commits")
                               for p in folder.rglob("*") if p.is_file()}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--student-id", required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"\d{6,12}", args.student_id):
        parser.error("student-id must be 6-12 digits")
    if platform.system() != "Linux":
        parser.error("run this baseline on Linux (WSL Ubuntu is supported)")
    missing = [t for t in ("git", "make", "cc", "python3", "strace") if not shutil.which(t)]
    if missing:
        parser.error("missing tools: " + ", ".join(missing))
    output = ROOT / "work/e3"
    output.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix=datetime.now().strftime("%Y%m%d-%H%M%S") + "-", dir=output))
    lab = Lab(directory)
    status, error = "PASSED", None
    try:
        dump(directory / "environment.json", environment(args.student_id))
        md_rd(lab)
        commits(lab, args.student_id)
    except Exception as exception:
        status, error = "FAILED", f"{type(exception).__name__}: {exception}"
        print(error, flush=True)
    dump(directory / "summary.json", {"status": status, "error": error,
         "assertions": lab.assertions, "provenance": "EXECUTION",
         "manual_oracles": ["fixtures/md-rd/oracle.json", "fixtures/commits/oracle.json"]})
    # Deliverable manifest excludes disposable sources, build products and live .git.
    paths = list((directory / "logs").glob("*")) + list((directory / "md-rd").glob("trace.*"))
    paths += [p for p in directory.iterdir() if p.is_file()]
    dump(directory / "manifest.json", {p.relative_to(directory).as_posix(): sha256(p)
                                       for p in sorted(paths) if p.name != "manifest.json"})
    print("EVIDENCE_DIR=" + str(directory), flush=True)
    return 0 if status == "PASSED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
