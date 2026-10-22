"""Standard-library integrity and isolated runtime support for the portable skill."""

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path, PurePosixPath


def bundle_files(root):
    manifest_path = root / "BUNDLE_MANIFEST.json"
    if manifest_path.is_symlink() or manifest_path.stat().st_size > 65536:
        raise ValueError("Bundle manifest exceeds its size limit.")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("Bundle manifest must be an object.")
    entries = manifest.get("files", [])
    if (
        manifest.get("schema_version") != 1
        or not isinstance(entries, list)
        or not 1 <= len(entries) <= 40
    ):
        raise ValueError("Bundle manifest is unsupported or incomplete.")
    paths = []
    total = 0
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            raise ValueError("Bundle entry must have a path.")
        relative = entry["path"]
        parts = PurePosixPath(relative).parts
        if (
            not parts
            or relative == "."
            or PurePosixPath(relative).as_posix() != relative
            or PurePosixPath(relative).is_absolute()
            or ".." in parts
            or "\\" in relative
            or ":" in relative
            or "\0" in relative
        ):
            raise ValueError("Bundle contains an unsafe path.")
        path = root / relative
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError("Bundle links are unsupported.")
        size = path.stat().st_size
        total += size
        if size > 2 * 1024 * 1024 or total > 8 * 1024 * 1024:
            raise ValueError("Bundle exceeds its size limit.")
        if not isinstance(entry.get("sha256"), str) or not re.fullmatch(
            r"[0-9a-f]{64}", entry["sha256"]
        ):
            raise ValueError("Bundle digest is malformed.")
        raw = path.read_bytes()
        if len(raw) != size or hashlib.sha256(raw).hexdigest() != entry["sha256"]:
            raise ValueError("Bundle contents failed integrity verification.")
        paths.append(relative)
    if len(set(paths)) != len(paths):
        raise ValueError("Bundle contains duplicate paths.")
    required = {"SKILL.md", "install.py", "scripts/repotrail.py", "runtime/manifest.json"}
    if not required <= set(paths):
        raise ValueError("Bundle is missing a required component.")
    return manifest, paths


def clean_environment(*, credentials=False):
    environment = dict(os.environ)
    for name in list(environment):
        upper = name.upper()
        if upper in {"PYTHONPATH", "PYTHONHOME"} or upper.startswith("GIT_"):
            environment.pop(name, None)
        elif not credentials and (
            upper.endswith("_API_KEY") or upper in {"GH_TOKEN", "GITHUB_TOKEN"}
        ):
            environment.pop(name, None)
    environment["LANGSMITH_TRACING"] = "false"
    return environment


def python_command():
    candidates = [[sys.executable], ["py", "-3.14"], ["python3.14"]]
    for candidate in candidates:
        try:
            check = subprocess.run(
                [*candidate, "-I", "-c", "import sys; print(sys.version_info[:2] == (3, 14))"],
                capture_output=True,
                text=True,
                timeout=10,
                env=clean_environment(),
            )
            if check.returncode == 0 and check.stdout.strip() == "True":
                return candidate
        except OSError, subprocess.TimeoutExpired:
            continue
    raise ValueError("Python3.14 is required. Install it, then invoke the skill again.")


def default_cache():
    if os.name == "nt":
        return (
            Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local"))
            / "RepoTrail/skill-runtime"
        )
    return (
        Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "repotrail/skill-runtime"
    )


def target_root(value):
    target = Path(value).expanduser().resolve(strict=True)
    result = subprocess.run(
        [
            "git",
            "-c",
            "core.fsmonitor=false",
            "-c",
            "core.hooksPath=",
            "--no-optional-locks",
            "rev-parse",
            "--path-format=absolute",
            "--show-toplevel",
        ],
        cwd=target,
        env=clean_environment(),
        capture_output=True,
        timeout=10,
    )
    if result.returncode or len(result.stdout) > 16384:
        raise ValueError(
            "Select an existing local Git working-tree root; no clone/fetch is performed."
        )
    actual = Path(result.stdout.decode("utf-8").rstrip("\r\n")).resolve(strict=True)
    if actual != target:
        raise ValueError("Select the Git working-tree root, rather than a subdirectory.")
    return actual


def ensure_runtime(root, cache, repository, *, api=False):
    bundle, _ = bundle_files(root)
    manifest = json.loads((root / "runtime/manifest.json").read_text(encoding="utf-8"))
    wheel_name = manifest["wheel"]
    if Path(wheel_name).name != wheel_name or not wheel_name.endswith(".whl"):
        raise ValueError("Runtime wheel name is invalid.")
    wheel = root / "runtime" / wheel_name
    if hashlib.sha256(wheel.read_bytes()).hexdigest() != manifest["sha256"]:
        raise ValueError("Bundled runtime wheel failed integrity verification.")
    cache = cache.expanduser().resolve()
    if repository is not None and cache.is_relative_to(repository):
        raise ValueError("Runtime installation must be outside the inspected repository.")
    key = hashlib.sha256((json.dumps(bundle, sort_keys=True) + str(api)).encode()).hexdigest()[:24]
    cache.mkdir(parents=True, exist_ok=True)
    runtime = cache / key
    python = runtime / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    ready = runtime / ".repotrail-ready.json"
    lock = cache / (key + ".lock")
    deadline = time.monotonic() + 30
    while True:
        try:
            descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(descriptor)
            break
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise ValueError(
                    "Another setup holds the runtime lock; retry later. A stale lock may need manual removal."
                ) from None
            time.sleep(0.2)
    try:
        if ready.is_file() and python.is_file():
            if json.loads(ready.read_text()) == {"key": key, "version": manifest["version"]}:
                return python, True
        if runtime.exists():
            raise ValueError(
                "Existing runtime is incomplete; choose a fresh --cache-dir or remove that incomplete runtime."
            )
        interpreter = python_command()
        print("RepoTrail: preparing an isolated runtime from the verified bundle.", file=sys.stderr)
        # Temporary setup and its final destination are both verified children of cache.
        with tempfile.TemporaryDirectory(prefix="repotrail-setup-", dir=cache) as staging:
            staging_path = Path(staging).resolve()
            if not staging_path.is_relative_to(cache) or not runtime.resolve().is_relative_to(
                cache
            ):
                raise ValueError("Runtime setup destination escaped its cache.")
            environment_path = staging_path / "environment"
            environment = clean_environment()
            commands = [
                [*interpreter, "-I", "-m", "venv", str(environment_path)],
            ]
            executable = environment_path / (
                "Scripts/python.exe" if os.name == "nt" else "bin/python"
            )
            commands.append(
                [
                    str(executable),
                    "-I",
                    "-m",
                    "pip",
                    "--isolated",
                    "install",
                    "--disable-pip-version-check",
                    "--no-input",
                    "--only-binary=:all:",
                    "--index-url",
                    "https://pypi.org/simple",
                    "--timeout",
                    "15",
                    "--retries",
                    "1",
                    "-r",
                    str(root / "runtime/requirements-core.txt"),
                    str(wheel) + ("[ai]" if api else ""),
                ]
            )
            for command in commands:
                result = subprocess.run(
                    command, cwd=root, env=environment, capture_output=True, timeout=240
                )
                if result.returncode:
                    raise ValueError(
                        "Automatic setup failed. Check Python3.14, PyPI access, and permission to write the isolated cache."
                    )
            (environment_path / ".repotrail-ready.json").write_text(
                json.dumps({"key": key, "version": manifest["version"]}), encoding="utf-8"
            )
            os.replace(environment_path, runtime)
        return python, False
    finally:
        lock.unlink(missing_ok=True)
