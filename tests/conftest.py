"""Isolated Git fixtures: only these temporary repositories receive fixture commits."""

import os
import subprocess
from pathlib import Path

import pytest


def git_command(root: Path, *args: str) -> bytes:
    result = subprocess.run(
        [
            "git",
            "-c",
            "core.fsmonitor=false",
            "-c",
            "core.hooksPath=",
            "-c",
            "init.templateDir=",
            "-c",
            "user.name=RepoTrail Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            *args,
        ],
        cwd=root,
        check=False,
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    return result.stdout


def write_file(root: Path, name: str, content: str = "fixture\n") -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def isolated_git_environment(monkeypatch, tmp_path):
    # Do not use the user's ignore rules, hooks, identities, or redirected Git index.
    for key in os.environ:
        if key.upper().startswith("GIT_"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "no-global-config"))
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "demo repo Ω"
    root.mkdir()
    git_command(root, "init", "-b", "main")
    return root


@pytest.fixture
def ordinary_repo(repo):
    write_file(repo, ".gitignore", "ignored.txt\nignored-dir/\n")
    write_file(repo, "tracked.py", "print('never execute')\n")
    write_file(repo, "deleted.py")
    write_file(repo, "tracked ignored.txt")
    write_file(repo, ".env", "not-a-real-secret\n")
    write_file(repo, "build/generated.py")
    git_command(repo, "add", ".")
    git_command(repo, "commit", "-m", "fixture base")
    git_command(repo, "update-index", "--assume-unchanged", "tracked.py")
    (repo / "deleted.py").unlink()
    write_file(repo, "staged.py")
    git_command(repo, "add", "staged.py")
    write_file(repo, ".gitignore", "ignored.txt\nignored-dir/\ntracked ignored.txt\n")
    for name in [
        "notes with spaces Ω.txt",
        ".env.example",
        "ignored.txt",
        "ignored-dir/a.py",
        "credentials.json",
        "key.pem",
        "node_modules/a.py",
        ".local/state.json",
        "nested/.venv/a.py",
        "custom-runtime/pyvenv.cfg",
        "custom-runtime/app.py",
    ]:
        write_file(repo, name)
    # Executing an input Python file would leave this marker; inspection must never do so.
    write_file(
        repo,
        "trap.py",
        "from pathlib import Path\nPath('EXECUTED').touch()\nraise RuntimeError('do not run')\n",
    )
    return repo
