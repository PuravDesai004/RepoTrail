import hashlib

import pytest
from conftest import git_command, write_file
from pydantic import ValidationError

from repotrail.demo import build_demo
from repotrail.evidence import get_project_overview, get_symbol_context, read_eligible
from repotrail.git import InventoryError
from repotrail.history import compare_revisions, recent_history, resolve_commit
from repotrail.inventory import inspect_repository
from repotrail.product_models import EvidenceLimits, SourceExcerpt


@pytest.fixture
def demo(tmp_path):
    root = tmp_path / "synthetic shop Ω"
    base, head = build_demo(root)
    return root, base, head


def test_overview_context_and_real_source_references(demo):
    root, _base, head = demo
    overview = get_project_overview(root)
    assert overview.packaging.name == "tiny-shop"
    assert overview.packaging.scripts == {"tiny-shop": "pricing:main"}
    assert overview.packaging.dependencies == []
    assert overview.entry_points[0].target == "pricing:main"
    assert overview.git.head_commit == head
    assert "working only.py" in [file.path for file in overview.source_map.files]
    context = get_symbol_context(root, "pricing.py", "total_price")
    assert context.status == "found" and context.complete
    excerpt = context.excerpt
    assert excerpt.reference.start_line == 1 and excerpt.reference.end_line == 3
    assert excerpt.lines[2].text == "    return sum(prices) - discount"
    assert (
        excerpt.reference.sha256 == hashlib.sha256((root / "pricing.py").read_bytes()).hexdigest()
    )
    assert excerpt.reference.commit_oid is None and excerpt.reference.source_kind == "working_tree"
    assert SourceExcerpt.model_validate_json(excerpt.model_dump_json()) == excerpt
    bad = excerpt.model_dump()
    bad["reference"]["end_line"] += 1
    with pytest.raises(ValidationError):
        SourceExcerpt.model_validate(bad)


def test_context_ambiguity_decorators_and_truncation(repo):
    write_file(
        repo, "a.py", "@decorator\ndef repeated():\n    return 1\n\ndef repeated():\n    return 2\n"
    )
    ambiguous = get_symbol_context(repo, "a.py", "repeated")
    assert ambiguous.status == "ambiguous" and ambiguous.definition_lines == [2, 5]
    exact = get_symbol_context(repo, "a.py", "repeated", 2)
    assert exact.excerpt.reference.start_line == 1 and exact.excerpt.lines[0].text == "@decorator"
    partial = get_symbol_context(
        repo, "a.py", "repeated", 2, limits=EvidenceLimits(max_context_bytes=12)
    )
    assert not partial.complete and partial.excerpt.reference.end_line == 1
    assert partial.diagnostics[0].code == "context_limit"
    assert get_symbol_context(repo, "a.py", "absent").status == "not_found"
    excluded = get_symbol_context(repo, ".env.private.py", "repeated")
    assert excluded.status == "unavailable" and excluded.excerpt is None
    assert excluded.diagnostics[0].code == "ineligible_path"
    for invalid in ["../a.py", "C:/outside.py", "/outside.py", "x\\a.py"]:
        with pytest.raises(InventoryError):
            get_symbol_context(repo, invalid, "repeated")


def test_stale_hash_and_oversize_metadata(repo):
    write_file(repo, "a.py", "def f():\n    pass\n")
    inventory = inspect_repository(repo)
    with pytest.raises(InventoryError, match="hash is stale"):
        read_eligible(repo, "a.py", inventory, 100, expected_sha256="0" * 64)
    write_file(repo, "README.md", "x" * 30)
    result = get_project_overview(repo, limits=EvidenceLimits(max_document_bytes=10))
    assert not result.complete and result.diagnostics[0].code == "file_too_large"


def test_endpoint_changes_history_and_no_worktree_writes(demo):
    root, base, head = demo
    status = git_command(root, "status", "--porcelain=v1", "-z")
    before = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
    history = recent_history(root)
    assert history.complete and len(history.commits) == 2 and history.commits[0].oid == head
    changes = compare_revisions(root, base, head)
    paths = {file.new_path or file.old_path: file for file in changes.files}
    assert changes.rename_policy == "no_renames_delete_add"
    assert paths["notes old.txt"].status == "D" and paths["notes new Ω.txt"].status == "A"
    assert paths["added module.py"].status == "A"
    assert paths["asset.bin"].binary and not changes.complete
    assert ".env" not in paths and changes.excluded_changes == 1
    pricing = paths["pricing.py"]
    assert [symbol.qualified_name for symbol in pricing.old_symbols] == ["total_price"]
    assert [symbol.qualified_name for symbol in pricing.new_symbols] == ["total_price"]
    assert pricing.hunks[0].old_excerpt.reference.commit_oid == base
    assert pricing.hunks[0].new_excerpt.reference.commit_oid == head
    assert "Unstaged working-tree note" not in pricing.hunks[0].patch
    assert "working only.py" not in paths
    assert changes.status_counts == {"A": 2, "D": 1, "M": 2}
    assert git_command(root, "status", "--porcelain=v1", "-z") == status
    assert {path: path.read_bytes() for path in root.rglob("*") if path.is_file()} == before


def test_revision_bounds_invalid_refs_empty_and_unborn(repo, demo):
    assert recent_history(repo).commits == []
    with pytest.raises(InventoryError, match="local commit"):
        compare_revisions(repo, "HEAD", "HEAD")
    root, base, head = demo
    assert compare_revisions(root, head, head).files == []
    assert not recent_history(root, limit=1).complete
    assert resolve_commit(root, "HEAD~1") == base
    for ref in ["--help", "does-not-exist", "-c core.fsmonitor=evil"]:
        with pytest.raises(InventoryError, match="local commit"):
            resolve_commit(root, ref)
    with pytest.raises(InventoryError, match="changed-file limit"):
        compare_revisions(root, base, head, limits=EvidenceLimits(max_changed_files=1))
    with pytest.raises(InventoryError, match="patch/context byte limit"):
        compare_revisions(root, base, head, limits=EvidenceLimits(max_patch_context_bytes=10))


def test_historical_deleted_python_and_index_symlink_not_read(repo):
    write_file(repo, "gone.py", "def old():\n    return 1\n")
    write_file(repo, "link.py", "target.py\n")
    git_command(repo, "add", ".")
    object_id = git_command(repo, "hash-object", "-w", "link.py").decode().strip()
    git_command(repo, "update-index", "--cacheinfo", f"120000,{object_id},link.py")
    git_command(repo, "commit", "-m", "fixture old")
    base = git_command(repo, "rev-parse", "HEAD").decode().strip()
    (repo / "gone.py").unlink()
    (repo / "link.py").unlink()
    git_command(repo, "add", "-A")
    git_command(repo, "commit", "-m", "fixture delete")
    head = git_command(repo, "rev-parse", "HEAD").decode().strip()
    result = compare_revisions(repo, base, head)
    assert result.excluded_changes == 1 and len(result.files) == 1
    assert result.files[0].old_symbols[0].qualified_name == "old"
    assert result.files[0].hunks[0].old_excerpt.reference.source_kind == "git_blob"
    assert result.files[0].hunks[0].new_excerpt is None
