"""Opt-in fresh extracted skill setup, without host configuration or provider calls."""

import hashlib
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path
from uuid import uuid4


def run(command, cwd, environment, allowed=(0,)):
    result = subprocess.run(
        command, cwd=cwd, env=environment, capture_output=True, text=True, timeout=300
    )
    if result.returncode not in allowed:
        raise RuntimeError(
            f"Skill verification failed ({result.returncode}): {result.stderr[-1500:]}"
        )
    return result.stdout


def main():
    project = Path(__file__).resolve().parents[1]
    archive = project / "downloads/repotrail-skill.zip"
    raw = archive.read_bytes()
    assert (
        hashlib.sha256(raw).hexdigest() == archive.with_suffix(".zip.sha256").read_text().split()[0]
    )
    directory = project / ".local/skill-tests" / uuid4().hex
    directory.mkdir(parents=True)
    with zipfile.ZipFile(archive) as bundle:
        assert len(bundle.namelist()) <= 40 and bundle.testzip() is None
        for entry in bundle.infolist():
            destination = (directory / entry.filename).resolve()
            assert (
                destination.is_relative_to(directory.resolve())
                and entry.file_size <= 2 * 1024 * 1024
            )
        bundle.extractall(directory)
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().endswith("_API_KEY")
        and not key.upper().startswith("GIT_")
        and key.upper() not in {"PYTHONPATH", "PYTHONHOME", "GH_TOKEN", "GITHUB_TOKEN"}
    }
    environment["LANGSMITH_TRACING"] = "false"
    # Everything below runs from the user's project parent, outside the source checkout.
    outside = project.parent
    homes = {}
    for host in ("codex", "claude"):
        homes[host] = json.loads(
            run(
                [
                    sys.executable,
                    "-I",
                    str(directory / "repotrail/install.py"),
                    "--host",
                    host,
                    "--destination",
                    str(directory / host / "skills"),
                ],
                outside,
                environment,
            )
        )
    launcher = Path(homes["codex"]["installed"]) / "scripts/repotrail.py"
    common = [sys.executable, "-I", str(launcher), "--cache-dir", str(directory / "runtime-cache")]
    setup = json.loads(run(common + ["--setup-only", "--repo", str(outside)], outside, environment))
    assert not setup["api_enabled"] and not setup["reused"]
    python = setup["runtime_python"]
    fixture = directory / "synthetic"
    run([python, "-I", "-m", "repotrail", "demo", str(fixture), "--json"], outside, environment)

    def evidence(*arguments):
        return json.loads(
            run(common + ["--repo", str(fixture), *arguments], outside, environment, (0, 1))
        )

    def status():
        return run(
            [
                "git",
                "-c",
                "core.fsmonitor=false",
                "--no-optional-locks",
                "-C",
                str(fixture),
                "status",
                "--porcelain=v1",
            ],
            outside,
            environment,
        )

    before = status()
    commit = evidence("--mode", "commit", "--ref", "HEAD")
    assert commit["document_type"] == "agent_evidence" and commit["sources"]
    assert "Unstaged working-tree note" not in json.dumps(commit)
    first = evidence("--mode", "whole", "--page-size", "1")
    second = evidence("--mode", "whole", "--page-size", "1", "--cursor", first["next_cursor"])
    assert second["next_cursor"] is None and second["metadata"]["returned"] == 1
    source = evidence(
        "--mode",
        "source",
        "--ref",
        commit["target_oid"],
        "--path",
        "pricing.py",
        "--start-line",
        "1",
        "--end-line",
        "3",
    )
    assert source["sources"][0]["reference"]["end_line"] == 3
    claude_launcher = Path(homes["claude"]["installed"]) / "scripts/repotrail.py"
    claude = json.loads(
        run(
            [
                sys.executable,
                "-I",
                str(claude_launcher),
                "--repo",
                str(fixture),
                "--cache-dir",
                str(directory / "runtime-cache"),
                "--mode",
                "commit",
            ],
            outside,
            environment,
            (1,),
        )
    )
    assert claude["target_oid"] == commit["target_oid"]
    reused = json.loads(
        run(common + ["--setup-only", "--repo", str(fixture)], outside, environment)
    )
    assert reused["reused"] and reused["runtime_python"] == python
    packages = json.loads(
        run([python, "-I", "-m", "pip", "list", "--format=json"], outside, environment)
    )
    assert not any(
        item["name"].startswith(("langchain", "langgraph", "groq", "google-genai"))
        for item in packages
    )
    assert before == status()
    assert not (fixture / "EXECUTED").exists()
    report = {
        "bundle_sha256": hashlib.sha256(raw).hexdigest(),
        "directory": str(directory.relative_to(project)),
        "fresh_setup": "passed",
        "cache_reuse": "passed",
        "codex_install_and_launcher": "passed",
        "claude_install_and_launcher": "passed",
        "commit_source_and_paged_history": "passed",
        "outside_checkout_invocation": True,
        "inspected_repository_unchanged": True,
        "provider_calls": 0,
        "optional_ai_packages_installed": False,
        "host_ui_discovery": "not exercised; installer and launchers verified",
    }
    (directory / "commit.json").write_text(json.dumps(commit, indent=2) + "\n", encoding="utf-8")
    (project / ".local/skill_verification.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
