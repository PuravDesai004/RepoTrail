"""Build delivery check: clean local environment, commands run outside the checkout."""

import json
import os
import subprocess
import venv
from pathlib import Path
from uuid import uuid4


def checked(args, *, cwd, environment, allowed=(0,)):
    result = subprocess.run(
        args, cwd=cwd, env=environment, capture_output=True, text=True, timeout=240
    )
    if result.returncode not in allowed:
        raise RuntimeError(
            f"Wheel check command failed ({result.returncode}): {result.stderr[-3000:]}"
        )
    return result.stdout


def main():
    project = Path(__file__).resolve().parents[1]
    wheels = sorted((project / "dist").glob("repotrail-*.whl"))
    if len(wheels) != 1:
        raise RuntimeError("Build exactly one current wheel in dist first.")
    directory = project / ".local" / "wheel-tests" / uuid4().hex
    directory.mkdir(parents=True)
    environment_path = directory / "environment"
    venv.EnvBuilder(with_pip=True).create(environment_path)
    python = environment_path / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    console = environment_path / ("Scripts/repotrail.exe" if os.name == "nt" else "bin/repotrail")
    environment = dict(os.environ)
    for name in ("PYTHONPATH", "GEMINI_API_KEY", "GOOGLE_API_KEY"):
        environment.pop(name, None)
    environment["LANGSMITH_TRACING"] = "false"
    outside = project.parent
    checked(
        [str(python), "-m", "pip", "install", "--disable-pip-version-check", str(wheels[0])],
        cwd=project,
        environment=environment,
    )
    help_text = checked([str(console), "--help"], cwd=outside, environment=environment)
    assert "overview" in help_text and "resume" in help_text
    fixture = directory / "synthetic"
    checked(
        [str(python), "-I", "-m", "repotrail", "demo", str(fixture), "--json"],
        cwd=outside,
        environment=environment,
    )
    core = json.loads(
        checked(
            [str(python), "-I", "-m", "repotrail", "overview", str(fixture), "--json"],
            cwd=outside,
            environment=environment,
        )
    )
    assert core["packaging"]["name"] == "tiny-shop"
    # Deterministic commands worked in a core-only environment before adding extras.
    checked(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            str(wheels[0]) + "[ai,mcp]",
        ],
        cwd=project,
        environment=environment,
    )
    checked([str(python), "-m", "pip", "check"], cwd=outside, environment=environment)
    paused = json.loads(
        checked(
            [
                str(console),
                "explain",
                str(fixture),
                "--run-id",
                "wheel-pause",
                "--pause-before-generate",
                "--state-dir",
                str(directory / "state"),
                "--json",
            ],
            cwd=outside,
            environment=environment,
            allowed=(1,),
        )
    )
    assert paused["status"] == "interrupted" and paused["attempts"] == 0
    resumed = json.loads(
        checked(
            [
                str(console),
                "resume",
                str(fixture),
                "wheel-pause",
                "--state-dir",
                str(directory / "state"),
                "--json",
            ],
            cwd=outside,
            environment=environment,
            allowed=(1,),
        )
    )
    assert resumed["status"] == "failed" and resumed["attempts"] == 0
    checked(
        [str(python), str(project / "examples" / "mcp_client.py"), str(fixture)],
        cwd=outside,
        environment=environment,
    )
    report = {
        "wheel": wheels[0].name,
        "clean_environment": str(environment_path.relative_to(project)),
        "invocation_cwd": str(outside),
        "outside_checkout": not outside.is_relative_to(project),
        "core_only_overview": "passed",
        "console_and_module": "passed",
        "ai_checkpoint_resume_missing_key": "passed",
        "mcp_official_stdio_client": "passed",
        "pip_check": "passed",
        "extras": ["ai", "mcp"],
    }
    output = project / ".local" / "wheel_verification.json"
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
