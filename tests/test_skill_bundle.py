"""Offline distribution checks: explicit copying, corruption rejection and runtime reuse."""

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from repotrail.demo import build_demo

PROJECT = Path(__file__).resolve().parents[1]
SKILL = PROJECT / "skills/repotrail"
spec = importlib.util.spec_from_file_location(
    "repotrail_bundle_test", SKILL / "scripts/bundle_support.py"
)
assert spec and spec.loader
support = importlib.util.module_from_spec(spec)
spec.loader.exec_module(support)


def installed(tmp_path, host):
    destination = tmp_path / host / "skills"
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            str(SKILL / "install.py"),
            "--host",
            host,
            "--destination",
            str(destination),
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    return destination / "repotrail", json.loads(result.stdout)


@pytest.mark.parametrize("host,invocation", [("codex", "$repotrail"), ("claude", "/repotrail")])
def test_installer_is_idempotent_and_preserves_different_skill(tmp_path, host, invocation):
    target, report = installed(tmp_path, host)
    assert report["invoke"] == invocation and not report["api_key_required"]
    assert installed(tmp_path, host)[1] == report
    source = target / "SKILL.md"
    source.write_text("existing user skill", encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            str(SKILL / "install.py"),
            "--host",
            host,
            "--destination",
            str(target.parent),
        ],
        capture_output=True,
        timeout=15,
    )
    assert result.returncode == 2 and source.read_text() == "existing user skill"


def test_bundle_rejects_corruption_traversal_and_does_not_copy_extra_files(tmp_path):
    copied = tmp_path / "bundle"
    shutil.copytree(SKILL, copied, ignore=shutil.ignore_patterns("__pycache__"))
    (copied / ".env").write_text("PRIVATE_TEST_MARKER", encoding="utf-8")
    destination = tmp_path / "destination"
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            str(copied / "install.py"),
            "--host",
            "codex",
            "--destination",
            str(destination),
        ],
        capture_output=True,
        timeout=15,
    )
    assert result.returncode == 0
    assert not (destination / "repotrail/.env").exists()
    (copied / "SKILL.md").write_text("corrupted", encoding="utf-8")
    with pytest.raises(ValueError, match="integrity"):
        support.bundle_files(copied)
    manifest_path = copied / "BUNDLE_MANIFEST.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["files"][0]["path"] = "../outside"
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="unsafe"):
        support.bundle_files(copied)


def test_cache_cannot_be_inside_inspected_repository(tmp_path):
    repository = tmp_path / "repo"
    repository.mkdir()
    cache = repository / "forbidden-cache"
    with pytest.raises(ValueError, match="outside"):
        support.ensure_runtime(SKILL, cache, repository)
    assert not cache.exists()


def test_ready_runtime_reuse_needs_no_setup_or_network(tmp_path, monkeypatch):
    bundle, _ = support.bundle_files(SKILL)
    manifest = json.loads((SKILL / "runtime/manifest.json").read_text())
    key = hashlib.sha256((json.dumps(bundle, sort_keys=True) + "False").encode()).hexdigest()[:24]
    runtime = tmp_path / key
    executable = runtime / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    executable.parent.mkdir(parents=True)
    executable.touch()
    (runtime / ".repotrail-ready.json").write_text(
        json.dumps({"key": key, "version": manifest["version"]})
    )

    def forbidden(*args, **kwargs):
        pytest.fail("A ready runtime must not perform setup/network subprocesses")

    monkeypatch.setattr(support.subprocess, "run", forbidden)
    assert support.ensure_runtime(SKILL, tmp_path, None) == (executable, True)


def test_native_environment_and_imports_need_no_provider(tmp_path, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "synthetic-private-value")
    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-private-value")
    monkeypatch.setenv("GIT_DIR", "untrusted-routing")
    clean = support.clean_environment()
    assert "GROQ_API_KEY" not in clean and "GEMINI_API_KEY" not in clean and "GIT_DIR" not in clean
    assert support.clean_environment(credentials=True)["GROQ_API_KEY"] == "synthetic-private-value"
    root = tmp_path / "fixture"
    build_demo(root)
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            str(SKILL / "scripts/repotrail.py"),
            "--repo",
            str(root),
            "--api",
            "--cache-dir",
            str(tmp_path / "unused"),
        ],
        capture_output=True,
        timeout=15,
    )
    assert result.returncode == 2 and not (tmp_path / "unused").exists()
    code = (
        "import sys; import repotrail.agent_evidence; "
        "assert not any(n.startswith(('langchain', 'langgraph', 'groq', 'google.genai')) for n in sys.modules)"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr


def test_download_bundle_matches_current_sources():
    result = subprocess.run(
        [sys.executable, str(PROJECT / "scripts/build_skill.py"), "--check"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr


def test_bundle_check_accepts_different_zip_encoding_but_rejects_content_drift(tmp_path):
    spec = importlib.util.spec_from_file_location(
        "repotrail_builder_test", PROJECT / "scripts/build_skill.py"
    )
    assert spec and spec.loader
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    shutil.copytree(
        SKILL, tmp_path / "skills/repotrail", ignore=shutil.ignore_patterns("__pycache__")
    )
    shutil.copytree(
        PROJECT / "src/repotrail",
        tmp_path / "src/repotrail",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    for name in ("README.md", "pyproject.toml"):
        shutil.copyfile(PROJECT / name, tmp_path / name)
    archive_path = tmp_path / "downloads/repotrail-skill.zip"
    archive_path.parent.mkdir()
    with zipfile.ZipFile(PROJECT / "downloads/repotrail-skill.zip") as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}

    def rewrite():
        with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_STORED) as archive:
            for name, raw in entries.items():
                archive.writestr(name, raw)
        archive_path.with_suffix(".zip.sha256").write_text(
            hashlib.sha256(archive_path.read_bytes()).hexdigest() + "  repotrail-skill.zip\n"
        )

    rewrite()
    builder.check(tmp_path)
    entries["repotrail/README.md"] += b"unreviewed drift"
    rewrite()
    with pytest.raises(AssertionError):
        builder.check(tmp_path)
