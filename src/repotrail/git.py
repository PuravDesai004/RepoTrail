"""Read-only Git access with explicit environment, time, and output bounds."""

import os
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from pathlib import Path

from .models import GitIdentity

GIT_TIMEOUT_SECONDS = 10
MAX_GIT_OUTPUT_BYTES = 8 * 1024 * 1024
MAX_CANDIDATES = 20_000


class InventoryError(Exception):
    """An expected inspection failure that the CLI can render without a traceback."""


def run_git(root: Path, args: list[str], ok_codes: tuple[int, ...] = (0,)) -> tuple[bytes, int]:
    """Return bounded stdout and exit code; suppress potentially sensitive Git stderr."""
    safe_config = {"GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM"}
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith("GIT_") or key.upper() in safe_config
    }
    env.update(GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0", GIT_NO_LAZY_FETCH="1")
    executable = shutil.which("git")
    if executable is None:
        raise InventoryError("Git is unavailable; install Git and make it available on PATH.")
    command = [
        executable,
        "--no-optional-locks",
        "--no-lazy-fetch",
        "--no-replace-objects",
        "-c",
        "core.fsmonitor=false",
        "-c",
        "core.untrackedCache=false",
        "-c",
        "submodule.recurse=false",
        *args,
    ]
    try:
        with subprocess.Popen(
            command,
            cwd=root,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        ) as process:
            assert process.stdout is not None
            deadline = time.monotonic() + GIT_TIMEOUT_SECONDS
            with ThreadPoolExecutor(max_workers=1) as reader:
                pending = reader.submit(process.stdout.read, MAX_GIT_OUTPUT_BYTES + 1)
                try:
                    output = pending.result(timeout=GIT_TIMEOUT_SECONDS)
                    if len(output) > MAX_GIT_OUTPUT_BYTES:
                        raise InventoryError("Git output exceeds the 8 MiB safety limit.")
                    code = process.wait(timeout=max(0, deadline - time.monotonic()))
                except (FutureTimeout, subprocess.TimeoutExpired) as exc:
                    process.kill()
                    process.wait()
                    raise InventoryError(
                        f"Git command timed out after {GIT_TIMEOUT_SECONDS}s."
                    ) from exc
                except BaseException:
                    process.kill()
                    process.wait()
                    raise
    except FileNotFoundError as exc:
        raise InventoryError(
            "Git is unavailable; install Git and make it available on PATH."
        ) from exc
    except OSError as exc:
        raise InventoryError(
            "Unable to start or read Git; check repository access and Git installation."
        ) from exc
    if code not in ok_codes:
        raise InventoryError(
            f"Git {args[0]} failed (exit {code}); check repository access and integrity."
        )
    return output, code


def decode_git(raw: bytes) -> str:
    """Require UTF-8 paths/refs instead of returning lossy or unserializable names."""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise InventoryError(
            "Git returned a non-UTF-8 path or reference; this inventory requires UTF-8."
        ) from exc


def repository_root(supplied: str | Path) -> Path:
    """Validate an actual working-tree root, never silently widen a subdirectory."""
    try:
        root = Path(supplied).expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise InventoryError("Repository path does not exist or cannot be resolved.") from exc
    if not root.is_dir():
        raise InventoryError("Repository path must be a directory.")
    inside, code = run_git(root, ["rev-parse", "--is-inside-work-tree"], (0, 128))
    if code != 0 or inside.strip() != b"true":
        raise InventoryError(
            "Repository path must be a Git working-tree root (bare repositories are unsupported)."
        )
    raw, _ = run_git(root, ["rev-parse", "--path-format=absolute", "--show-toplevel"])
    actual = Path(decode_git(raw).removesuffix("\n")).resolve(strict=True)
    if actual != root:
        raise InventoryError(
            f"A subdirectory was supplied; use the Git working-tree root: {actual}"
        )
    return root


def git_identity(root: Path) -> GitIdentity:
    """Distinguish an attached commit, detached commit, and an unborn branch."""
    ref_raw, ref_code = run_git(root, ["symbolic-ref", "--quiet", "HEAD"], (0, 1))
    ref = decode_git(ref_raw).removesuffix("\n") if ref_code == 0 else None
    if ref is not None and not ref.startswith("refs/heads/"):
        raise InventoryError("HEAD refers outside refs/heads; unsupported repository state.")
    head_raw, head_code = run_git(
        root, ["rev-parse", "--verify", "--quiet", "HEAD^{commit}"], (0, 1)
    )
    head = decode_git(head_raw).strip() if head_code == 0 else None
    if head is None:
        if ref is None:
            raise InventoryError("HEAD cannot be resolved to a commit or an unborn branch.")
        _, exists = run_git(root, ["show-ref", "--verify", "--quiet", ref], (0, 1))
        if exists == 0:
            raise InventoryError("The current branch exists but its commit cannot be read.")
    return GitIdentity(
        head_commit=head,
        branch=ref.removeprefix("refs/heads/") if ref else None,
        detached=ref is None,
        unborn=head is None,
    )


def nul_records(raw: bytes) -> list[bytes]:
    """Split Git's unquoted paths on NUL, which cannot occur inside a filename."""
    if not raw:
        return []
    if not raw.endswith(b"\0"):
        raise InventoryError("Git returned incomplete NUL-delimited path output.")
    records = raw[:-1].split(b"\0")
    if len(records) > MAX_CANDIDATES:
        raise InventoryError(
            f"Git inventory exceeds the {MAX_CANDIDATES:,} candidate-record limit."
        )
    return records


def git_paths(
    root: Path, exclusions: tuple[list[str], list[str]]
) -> tuple[dict[str, bool], set[str]]:
    """Return index/untracked candidates and index links; index membership means tracked."""
    exclude_args, directory_ignores = exclusions
    raw, _ = run_git(root, ["ls-files", "--cached", "--stage", "-z", "--", *exclude_args])
    staged = nul_records(raw)
    paths: dict[str, bool] = {}
    links: set[str] = set()
    for record in staged:
        try:
            metadata, name = record.split(b"\t", 1)
            mode, _object_id, _stage = metadata.split(b" ")
        except ValueError as exc:
            raise InventoryError("Git returned malformed index entries.") from exc
        path = decode_git(name)
        paths[path] = True
        if mode in (b"120000", b"160000"):
            links.add(path)
    raw, _ = run_git(
        root,
        [
            "ls-files",
            "--others",
            "--exclude-standard",
            *directory_ignores,
            "-z",
            "--",
            *exclude_args,
        ],
    )
    untracked = nul_records(raw)
    if len(staged) + len(untracked) > MAX_CANDIDATES:
        raise InventoryError(
            f"Git inventory exceeds the {MAX_CANDIDATES:,} candidate-record limit."
        )
    for record in untracked:
        path = decode_git(record)
        if not path.endswith("/"):  # Git may report an embedded repository as a directory.
            paths.setdefault(path, False)
    return paths, links
