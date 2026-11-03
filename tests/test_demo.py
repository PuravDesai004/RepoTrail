"""Fixture setup cannot route writes to another repository through inherited Git env."""

from repotrail.demo import build_demo, fixture_git


def test_demo_ignores_inherited_git_routing(tmp_path, monkeypatch):
    other = tmp_path / "other"
    _, previous = build_demo(other)
    before = fixture_git(other, "status", "--porcelain=v1")
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(other))
    monkeypatch.setenv("GIT_INDEX_FILE", str(other / "injected-index"))
    root = tmp_path / "destination"
    base, head = build_demo(root)
    assert base != head and (root / ".git").is_dir()
    assert fixture_git(other, "rev-parse", "HEAD") == previous
    assert fixture_git(other, "status", "--porcelain=v1") == before
    assert not (other / "injected-index").exists()
