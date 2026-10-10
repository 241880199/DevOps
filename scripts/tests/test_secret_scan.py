"""Regression checks for Git filenames and secrets remaining in commit history."""
import os
import subprocess
import sys
from pathlib import Path

SCANNER = Path(__file__).resolve().parents[1] / "secret_scan.py"


def git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def repository(tmp_path):
    git(tmp_path, "init")
    git(tmp_path, "config", "user.name", "241880444")
    git(tmp_path, "config", "user.email", "241880444@localhost")
    git(tmp_path, "commit", "--allow-empty", "-m", "initial")
    return tmp_path


def scan(repo, *flags):
    return subprocess.run([sys.executable, str(SCANNER), str(repo), *flags],
                          capture_output=True, encoding="utf-8",
                          env={**os.environ, "PYTHONIOENCODING": "utf-8"})


def test_staged_unicode_filename_is_scanned_and_key_is_masked(tmp_path):
    repo = repository(tmp_path)
    secret = "sk-" + "a" * 24
    name = "配置.txt"
    (repo / name).write_text(secret, encoding="utf-8")
    git(repo, "add", name)
    result = scan(repo)
    assert result.returncode == 1
    assert name in result.stdout
    assert secret not in result.stdout
    assert "sk-a***" in result.stdout


def test_deleted_secret_remains_detectable_in_git_history(tmp_path):
    repo = repository(tmp_path)
    (repo / "leak.txt").write_text("sk-" + "b" * 24, encoding="utf-8")
    git(repo, "add", "leak.txt")
    git(repo, "commit", "-m", "synthetic leak in disposable repo")
    git(repo, "rm", "leak.txt")
    git(repo, "commit", "-m", "remove synthetic leak")
    assert scan(repo).returncode == 0
    result = scan(repo, "--history")
    assert result.returncode == 1
    assert "git-history" in result.stdout
