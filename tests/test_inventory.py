import json
import subprocess
import sys

import pytest
from conftest import git_command, write_file

import repotrail.inventory as inventory_module
from repotrail.analysis import analyze_python_repository
from repotrail.git import InventoryError
from repotrail.inventory import inspect_repository
from repotrail.models import Inventory


def test_ordinary_inventory(ordinary_repo):
    result = inspect_repository(ordinary_repo)
    files = {entry.path: entry for entry in result.files}
    assert list(files) == [
        ".env.example",
        ".gitignore",
        "notes with spaces Ω.txt",
        "staged.py",
        "tracked ignored.txt",
        "tracked.py",
        "trap.py",
    ]
    assert {name for name, entry in files.items() if entry.tracked} == {
        ".gitignore",
        "staged.py",
        "tracked ignored.txt",
        "tracked.py",
    }
    assert result.counts.model_dump() == {
        "total_files": 7,
        "tracked_files": 4,
        "untracked_files": 3,
    }
    assert files["notes with spaces Ω.txt"].extension == ".txt"
    assert result.git.branch == "main" and not result.git.detached and not result.git.unborn
    assert len(result.git.head_commit) == 40
    assert not (ordinary_repo / "EXECUTED").exists()
    assert Inventory.model_validate_json(result.model_dump_json()) == result


def test_empty_history_and_staged_file(repo):
    empty = inspect_repository(repo)
    assert empty.git.model_dump() == {
        "head_commit": None,
        "branch": "main",
        "detached": False,
        "unborn": True,
    }
    assert empty.counts.total_files == 0
    write_file(repo, "new.py")
    git_command(repo, "add", "new.py")
    staged = inspect_repository(repo)
    assert staged.files[0].tracked and staged.git.unborn


def test_detached_head(repo):
    write_file(repo, "app.py")
    git_command(repo, "add", ".")
    git_command(repo, "commit", "-m", "fixture")
    head = git_command(repo, "rev-parse", "HEAD").decode().strip()
    git_command(repo, "checkout", "--detach", head)
    identity = inspect_repository(repo).git
    assert identity.head_commit == head and identity.branch is None
    assert identity.detached and not identity.unborn


def test_invalid_paths_and_actual_root(repo, tmp_path):
    with pytest.raises(InventoryError, match="does not exist"):
        inspect_repository(tmp_path / "missing")
    with pytest.raises(InventoryError, match="must be a directory"):
        inspect_repository(write_file(tmp_path, "file.txt"))
    with pytest.raises(InventoryError, match="Git working-tree root"):
        inspect_repository(tmp_path)
    child = repo / "child directory"
    child.mkdir()
    with pytest.raises(InventoryError, match="subdirectory.*" + repo.name) as caught:
        inspect_repository(child)
    assert str(repo) in str(caught.value)
    with pytest.raises(InventoryError, match="Git working-tree root"):
        inspect_repository(repo / ".git")


def test_real_submodule_and_embedded_repository_are_not_traversed(repo):
    # An old-form submodule has its own .git directory, a parent gitlink, and
    # .gitmodules registration. Native Git commands avoid MSYS shell requirements.
    source = repo / "child"
    source.mkdir()
    git_command(source, "init", "-b", "main")
    write_file(source, "private_child.py")
    git_command(source, "add", ".")
    git_command(source, "commit", "-m", "fixture child")
    head = git_command(source, "rev-parse", "HEAD").decode().strip()
    write_file(
        repo, ".gitmodules", '[submodule "child"]\n\tpath = child\n\turl = ../fixture-child\n'
    )
    git_command(repo, "add", ".gitmodules")
    git_command(repo, "update-index", "--add", "--cacheinfo", f"160000,{head},child")
    embedded = repo / "embedded"
    embedded.mkdir()
    git_command(embedded, "init", "-b", "main")
    write_file(embedded, "also_private.py")
    paths = [entry.path for entry in inspect_repository(repo).files]
    assert paths == [".gitmodules"]
    assert analyze_python_repository(repo).files == []


def test_symlink_file_and_directory_not_traversed(repo, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    target = write_file(outside, "external.py")
    try:
        (repo / "linked-file.py").symlink_to(target)
        (repo / "linked-directory").symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"Windows symlink creation unavailable: {exc}")
    assert inspect_repository(repo).files == []
    assert analyze_python_repository(repo).files == []


def test_index_symlink_is_excluded_even_when_worktree_has_regular_file(repo):
    write_file(repo, "link.py", "normal file in a core.symlinks=false checkout\n")
    object_id = git_command(repo, "hash-object", "-w", "link.py").decode().strip()
    git_command(repo, "update-index", "--add", "--cacheinfo", f"120000,{object_id},link.py")
    assert inspect_repository(repo).files == []
    assert analyze_python_repository(repo).files == []


def test_tracked_directory_replaced_by_link_not_traversed(repo, tmp_path):
    original = write_file(repo, "package/a.py")
    git_command(repo, "add", ".")
    original.unlink()
    original.parent.rmdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    write_file(outside, "a.py")
    try:
        (repo / "package").symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"Windows directory symlink creation unavailable: {exc}")
    assert inspect_repository(repo).files == []
    assert analyze_python_repository(repo).files == []


def test_deterministic_cli_and_read_only(ordinary_repo):
    before_status = git_command(ordinary_repo, "status", "--porcelain=v1", "-z")
    before_contents = {
        path: path.read_bytes() for path in ordinary_repo.rglob("*") if path.is_file()
    }
    command = [sys.executable, "-m", "repotrail", "inspect", str(ordinary_repo), "--json"]
    first = subprocess.run(command, capture_output=True, check=True, timeout=30)
    second = subprocess.run(command, capture_output=True, check=True, timeout=30)
    assert first.stdout == second.stdout and first.stderr == second.stderr == b""
    assert Inventory.model_validate_json(first.stdout).counts.total_files == 7
    assert json.loads(first.stdout)["project_name"] == ordinary_repo.name
    assert str(ordinary_repo).encode() not in first.stdout
    assert git_command(ordinary_repo, "status", "--porcelain=v1", "-z") == before_status
    after_contents = {
        path: path.read_bytes() for path in ordinary_repo.rglob("*") if path.is_file()
    }
    assert after_contents == before_contents


def test_file_limit_returns_no_partial_json(repo):
    write_file(repo, "a.py")
    write_file(repo, "b.py")
    with pytest.raises(InventoryError, match="exceeds the 1 file limit"):
        inspect_repository(repo, max_files=1)
    command = [
        sys.executable,
        "-m",
        "repotrail",
        "inspect",
        str(repo),
        "--json",
        "--max-files",
        "1",
    ]
    result = subprocess.run(command, capture_output=True, timeout=30)
    assert result.returncode == 2 and result.stdout == b""
    assert b"file limit" in result.stderr and b"Traceback" not in result.stderr


@pytest.mark.parametrize("limit", [0, -1, 10_001])
def test_invalid_file_limit(repo, limit):
    with pytest.raises(InventoryError, match="File limit must be"):
        inspect_repository(repo, max_files=limit)


@pytest.mark.parametrize("initially_present", [False, True])
def test_eligibility_change_is_an_error(repo, monkeypatch, initially_present):
    path = write_file(repo, "changing.py")
    git_command(repo, "add", ".")
    if not initially_present:
        path.unlink()
    real = inventory_module.file_signature
    calls = 0

    def change_after_first_check(root, relative):
        nonlocal calls
        signature = real(root, relative)
        calls += 1
        if calls == 1:
            if initially_present:
                path.unlink()
            else:
                path.write_text("appeared", encoding="utf-8")
        return signature

    monkeypatch.setattr(inventory_module, "file_signature", change_after_first_check)
    with pytest.raises(InventoryError, match="eligibility or metadata changed"):
        inspect_repository(repo)


def test_index_change_is_an_error(repo, monkeypatch):
    write_file(repo, "a.py")
    real = inventory_module.git_paths
    calls = 0

    def stage_after_first_list(root, excludes):
        nonlocal calls
        result = real(root, excludes)
        calls += 1
        if calls == 1:
            git_command(root, "add", "a.py")
        return result

    monkeypatch.setattr(inventory_module, "git_paths", stage_after_first_list)
    with pytest.raises(InventoryError, match="Repository state changed"):
        inspect_repository(repo)


def test_git_environment_and_fsmonitor_are_not_executed(repo, monkeypatch):
    write_file(repo, "app.py")
    git_command(repo, "add", ".")
    git_command(repo, "config", "core.fsmonitor", "this-command-must-never-run")
    monkeypatch.setenv("GIT_DIR", str(repo / "nonexistent"))
    monkeypatch.setenv("GIT_INDEX_FILE", str(repo / "injected-index"))
    assert [entry.path for entry in inspect_repository(repo).files] == ["app.py"]
    assert not (repo / "injected-index").exists()


def test_git_info_exclude_is_respected(repo):
    write_file(repo, ".git/info/exclude", "local-only.txt\n")
    write_file(repo, "local-only.txt")
    assert inspect_repository(repo).files == []


def test_global_git_ignore_rules_are_respected(repo, tmp_path, monkeypatch):
    ignore = write_file(tmp_path, "global-ignore", "global-only.txt\n")
    config = write_file(
        tmp_path, "global-config", f'[core]\n\texcludesFile = "{ignore.as_posix()}"\n'
    )
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    write_file(repo, "global-only.txt")
    assert inspect_repository(repo).files == []


def test_bare_repository_is_rejected(tmp_path):
    bare = tmp_path / "bare.git"
    bare.mkdir()
    git_command(bare, "init", "--bare")
    with pytest.raises(InventoryError, match="bare repositories are unsupported"):
        inspect_repository(bare)


def test_intent_to_add_is_tracked(repo):
    write_file(repo, "planned.py")
    git_command(repo, "add", "--intent-to-add", "planned.py")
    result = inspect_repository(repo)
    assert result.files[0].tracked and result.counts.tracked_files == 1
