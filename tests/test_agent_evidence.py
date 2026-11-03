"""Historical packets preserve immutable scope, exclusions, and honest history coverage."""

import base64
import subprocess

import pytest
from conftest import git_command, write_file

from repotrail.agent_evidence import get_commit_evidence, get_history_page, get_revision_source
from repotrail.demo import build_demo
from repotrail.git import InventoryError


def test_commit_snapshot_and_source_ignore_dirty_worktree(tmp_path):
    root = tmp_path / "history with spaces Ω"
    base, head = build_demo(root)
    before = git_command(root, "status", "--porcelain=v1")
    packet = get_commit_evidence(root, head)
    assert packet.target_oid == head and packet.metadata["parents"] == [base]
    assert packet.metadata["comparison_policy"] == "first_parent"
    assert not packet.complete and packet.omissions
    assert all(s.reference.source_kind == "git_blob" for s in packet.sources)
    assert {s.reference.commit_oid for s in packet.sources} == {base, head}
    rendered = packet.model_dump_json()
    assert "Unstaged working-tree note" not in rendered and "SYNTHETIC_PLACEHOLDER" not in rendered
    assert "sum(prices) * (1 - discount)" in rendered and "sum(prices) - discount" in rendered
    source = get_revision_source(root, base, "pricing.py", start=2, end=3)
    assert source.complete
    excerpt = source.sources[0]
    assert [line.number for line in excerpt.lines] == [2, 3]
    assert excerpt.reference.commit_oid == base and excerpt.reference.end_line == 3
    assert packet.source_text_bytes <= 32768 and packet.metadata_bytes <= 32768
    assert len(packet.model_dump_json().encode()) <= 131072
    assert git_command(root, "status", "--porcelain=v1") == before
    assert git_command(root, "rev-parse", "HEAD").decode().strip() == head


@pytest.mark.parametrize("path", [".env", "../pricing.py", "C:/pricing.py", "missing.py"])
def test_historical_source_rejects_excluded_or_unsafe_paths(tmp_path, path):
    root = tmp_path / "source"
    _, head = build_demo(root)
    with pytest.raises((InventoryError, ValueError)):
        get_revision_source(root, head, path)


def test_binary_oversized_line_and_invalid_range_are_explicit(repo):
    write_file(repo, "large.py", "#" + "x" * 17000 + "\n")
    (repo / "binary.dat").write_bytes(b"\0binary")
    git_command(repo, "add", ".")
    git_command(repo, "commit", "-m", "unavailable source cases")
    for path, start in [("large.py", 1), ("binary.dat", 1), ("large.py", 2)]:
        with pytest.raises(InventoryError):
            get_revision_source(repo, "HEAD", path, start=start)


def test_paging_pins_target_and_rejects_cross_scope_cursors(tmp_path):
    root = tmp_path / "first"
    base, head = build_demo(root)
    first = get_history_page(root, page_size=1)
    assert first.next_cursor and first.metadata["commits"][0]["oid"] == head
    write_file(root, "later.txt")
    git_command(root, "add", "later.txt")
    git_command(root, "commit", "-m", "later commit")
    last = get_history_page(root, cursor=first.next_cursor, page_size=1)
    assert last.target_oid == head and last.metadata["commits"][0]["oid"] == base
    assert last.next_cursor is None and not last.metadata["has_more"]
    other = tmp_path / "second"
    build_demo(other)
    for target, ref, size in [(other, None, 1), (root, "HEAD", 1), (root, None, 2)]:
        with pytest.raises(InventoryError, match="cursor"):
            get_history_page(target, ref, cursor=first.next_cursor, page_size=size)
    for invalid in ["!", base64.urlsafe_b64encode(b"null").decode(), "x" * 2049]:
        with pytest.raises(InventoryError, match="cursor"):
            get_history_page(root, cursor=invalid, page_size=1)


def test_whole_history_can_continue_beyond_legacy_hundred(repo):
    # Fast-import creates owned fixture data; it never executes repository code.
    records = []
    for index in range(105):
        message = f"fixture {index}\n".encode()
        records.append(
            b"commit refs/heads/main\n"
            + f"mark :{index + 1}\n".encode()
            + b"committer Fixture <fixture@example.invalid> 1000000000 +0000\n"
            + f"data {len(message)}\n".encode()
            + message
            + b"\n"
        )
    result = subprocess.run(
        ["git", "-c", "core.hooksPath=", "fast-import", "--quiet"],
        cwd=repo,
        input=b"".join(records) + b"done\n",
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    cursor = None
    ids = []
    while True:
        page = get_history_page(repo, cursor=cursor)
        ids.extend(commit["oid"] for commit in page.metadata["commits"])
        assert not page.complete and page.metadata["coverage"] == "page_only"
        cursor = page.next_cursor
        if cursor is None:
            break
    assert len(ids) == len(set(ids)) == 105


def test_merge_first_parent_and_shallow_boundary_are_disclosed(repo):
    write_file(repo, "app.py", "value = 1\n")
    git_command(repo, "add", ".")
    git_command(repo, "commit", "-m", "root")
    base = git_command(repo, "rev-parse", "HEAD").decode().strip()
    assert get_commit_evidence(repo).metadata["parents"] == []
    git_command(repo, "checkout", "-b", "side")
    write_file(repo, "side.py", "side = True\n")
    git_command(repo, "add", ".")
    git_command(repo, "commit", "-m", "side")
    side = git_command(repo, "rev-parse", "HEAD").decode().strip()
    git_command(repo, "checkout", "main")
    git_command(repo, "merge", "--no-ff", "side", "-m", "merge fixture")
    merge = git_command(repo, "rev-parse", "HEAD").decode().strip()
    packet = get_commit_evidence(repo)
    assert packet.metadata["parents"] == [base, side]
    assert packet.metadata["comparison_policy"] == "first_parent"
    # Synthetic boundary: parent headers remain while Git traversal stops here.
    (repo / ".git/shallow").write_text(merge + "\n", encoding="ascii")
    page = get_history_page(repo)
    assert page.metadata["repository_shallow"] and page.metadata["returned"] == 1
    assert any("shallow" in omission for omission in page.omissions)
    assert get_commit_evidence(repo).metadata["parent_count"] == 2
