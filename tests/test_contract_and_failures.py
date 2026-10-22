import io
import stat
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

import repotrail.git as git_module
from repotrail.cli import main
from repotrail.git import InventoryError, decode_git, nul_records, run_git
from repotrail.inventory import excluded_path, file_signature
from repotrail.models import FileCounts, FileEntry, GitIdentity, Inventory


def test_contract_rejects_incoherent_counts_paths_and_history():
    identity = GitIdentity(head_commit=None, branch="main", detached=False, unborn=True)
    file = FileEntry(path="a.py", extension=".py", tracked=True)
    with pytest.raises(ValidationError, match="counts must reconcile"):
        Inventory(
            project_name="demo",
            git=identity,
            files=[file],
            counts=FileCounts(total_files=1, tracked_files=0, untracked_files=1),
        )
    with pytest.raises(ValidationError, match="sorted and unique"):
        Inventory(
            project_name="demo",
            git=identity,
            files=[file, file],
            counts=FileCounts(total_files=2, tracked_files=2, untracked_files=0),
        )
    with pytest.raises(ValidationError, match="unborn must agree"):
        GitIdentity(head_commit=None, branch="main", detached=False, unborn=False)
    with pytest.raises(ValidationError, match="extension must"):
        FileEntry(path="a.PY", extension=".PY", tracked=False)


@pytest.mark.parametrize("path", ["../outside.py", "/absolute.py", "a/../b.py", "a//b.py", ".", ""])
def test_contract_rejects_invalid_paths(path):
    with pytest.raises(ValidationError):
        FileEntry(path=path, extension=".py", tracked=True)


@pytest.mark.parametrize(
    "name",
    [
        ".env",
        ".env.production",
        "nested/.ENV",
        "private.KEY",
        "credentials.json",
        ".netrc",
        "id_ed25519",
        "pkg/.local/x.py",
        "pkg/DIST/x.py",
        "thing.egg-info/PKG-INFO",
    ],
)
def test_filename_exclusions(name):
    assert excluded_path(name)


@pytest.mark.parametrize(
    "name", [".env.example", "nested/.env.sample", ".env.template", "key.pub", "app.py"]
)
def test_harmless_templates_and_normal_names(name):
    assert not excluded_path(name)


def test_missing_git_is_a_friendly_cli_error(repo, monkeypatch, capsys):
    monkeypatch.setattr(git_module.shutil, "which", lambda _name: None)
    assert main(["inspect", str(repo), "--json"]) == 2
    output = capsys.readouterr()
    assert output.out == "" and "Git is unavailable" in output.err and "Traceback" not in output.err


def test_git_failure(repo, monkeypatch, capsys):
    process = MagicMock()
    process.stdout = io.BytesIO(b"")
    process.wait.return_value = 42
    context = MagicMock()
    context.__enter__.return_value = process
    monkeypatch.setattr(git_module.subprocess, "Popen", lambda *args, **kwargs: context)
    assert main(["inspect", str(repo), "--json"]) == 2
    assert "failed (exit 42)" in capsys.readouterr().err


def test_git_timeout_kills_process_and_returns_cli_error(repo, monkeypatch, capsys):
    process = MagicMock()
    process.stdout = io.BytesIO(b"true\n")
    process.wait.side_effect = [subprocess.TimeoutExpired("git", 10), 0]
    context = MagicMock()
    context.__enter__.return_value = process
    monkeypatch.setattr(git_module.subprocess, "Popen", lambda *args, **kwargs: context)
    assert main(["inspect", str(repo), "--json"]) == 2
    process.kill.assert_called_once()
    output = capsys.readouterr()
    assert output.out == "" and "timed out after 10s" in output.err


def test_git_stdout_read_timeout(repo, monkeypatch):
    process = MagicMock()
    process.stdout.read.side_effect = git_module.FutureTimeout()
    context = MagicMock()
    context.__enter__.return_value = process
    monkeypatch.setattr(git_module.subprocess, "Popen", lambda *args, **kwargs: context)
    with pytest.raises(InventoryError, match="timed out"):
        run_git(repo, ["ls-files"])
    process.kill.assert_called_once()


def test_git_output_and_candidate_limits(repo, monkeypatch):
    monkeypatch.setattr(git_module, "MAX_GIT_OUTPUT_BYTES", 8)
    process = MagicMock()
    process.stdout = io.BytesIO(b"x" * 9)
    context = MagicMock()
    context.__enter__.return_value = process
    monkeypatch.setattr(git_module.subprocess, "Popen", lambda *args, **kwargs: context)
    with pytest.raises(InventoryError, match="output exceeds"):
        run_git(repo, ["ls-files"])
    process.kill.assert_called_once()
    monkeypatch.setattr(git_module, "MAX_CANDIDATES", 2)
    with pytest.raises(InventoryError, match="candidate-record limit"):
        nul_records(b"a\0b\0c\0")


def test_git_path_protocol_and_unsafe_paths(tmp_path):
    assert nul_records(b"space name\0line\nname\0") == [b"space name", b"line\nname"]
    with pytest.raises(InventoryError, match="incomplete NUL"):
        nul_records(b"not terminated")
    with pytest.raises(InventoryError, match="non-UTF-8"):
        decode_git(b"\xff")
    with pytest.raises(InventoryError, match="unsafe"):
        file_signature(tmp_path, "../outside")


@pytest.mark.parametrize("mode,attributes", [(stat.S_IFLNK, 0), (stat.S_IFDIR, 0x400)])
def test_link_or_reparse_parent_is_rejected_before_descent(tmp_path, monkeypatch, mode, attributes):
    seen = []

    def linked_parent(path):
        seen.append(path)
        return SimpleNamespace(st_mode=mode, st_file_attributes=attributes)

    monkeypatch.setattr(Path, "lstat", linked_parent)
    assert file_signature(tmp_path, "linked/hidden.py") is None
    assert seen == [tmp_path / "linked"]


def test_subprocess_uses_argument_list_and_safe_environment(repo, monkeypatch):
    real = git_module.subprocess.Popen
    observed = []

    def capture(command, **kwargs):
        observed.append((command, kwargs))
        return real(command, **kwargs)

    monkeypatch.setattr(git_module.subprocess, "Popen", capture)
    run_git(repo, ["ls-files", "-z"])
    command, options = observed[0]
    assert isinstance(command, list) and Path(command[0]).is_absolute()
    assert options.get("shell", False) is False
    assert options["env"]["GIT_OPTIONAL_LOCKS"] == "0"
    assert "core.fsmonitor=false" in command
    assert "--no-lazy-fetch" in command
