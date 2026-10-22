"""Filename policy, filesystem eligibility, and inventory assembly; no source reads."""

import stat
from pathlib import Path, PurePosixPath

from .git import InventoryError, git_identity, git_paths, repository_root
from .models import FileCounts, FileEntry, Inventory

MAX_FILES = 10_000
EXCLUDED_DIRECTORIES = frozenset(
    {
        ".git",
        ".local",
        ".venv",
        "venv",
        "env",
        ".env",
        "__pycache__",
        "node_modules",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".cache",
        ".tox",
        ".nox",
        "build",
        "dist",
        "htmlcov",
        "site-packages",
        ".eggs",
        "vendor",
    }
)
CREDENTIAL_NAMES = frozenset(
    {
        ".env",
        ".netrc",
        "_netrc",
        ".pypirc",
        ".npmrc",
        "credentials",
        "credentials.json",
        "credentials.yaml",
        "credentials.yml",
        "secrets.json",
        "secrets.yaml",
        "secrets.yml",
        "id_rsa",
        "id_dsa",
        "id_ecdsa",
        "id_ed25519",
        "service-account.json",
    }
)
PRIVATE_KEY_SUFFIXES = frozenset({".pem", ".key", ".p12", ".pfx", ".ppk"})
ENV_TEMPLATES = frozenset({".env.example", ".env.sample", ".env.template"})


def excluded_directory(name: str) -> bool:
    """Match explicit cache/state directories and generated Python metadata."""
    lower = name.lower()
    return lower in EXCLUDED_DIRECTORIES or lower.endswith(".egg-info")


def excluded_path(path: str) -> bool:
    """Apply a filename filter; this does not detect secrets in arbitrary content."""
    parts = PurePosixPath(path).parts
    if any(excluded_directory(part) for part in parts[:-1]):
        return True
    name = parts[-1].lower()
    if name in ENV_TEMPLATES:
        return False
    return (
        name in CREDENTIAL_NAMES
        or name.startswith(".env.")
        or PurePosixPath(name).suffix in PRIVATE_KEY_SUFFIXES
    )


def git_exclusions() -> tuple[list[str], list[str]]:
    """Prune known directory names in Git before it enumerates untracked descendants."""
    names = [*sorted(EXCLUDED_DIRECTORIES), "*.egg-info"]
    pathspecs = [f":(exclude,glob,icase)**/{name}/**" for name in names]
    # Directory ignore rules prune traversal; pathspecs also filter indexed entries.
    # Character classes make ignore matching case-insensitive on every platform.
    patterns = [
        "".join(f"[{char}{char.upper()}]" if char.isalpha() else char for char in name)
        for name in names
    ]
    return pathspecs, [f"--exclude={pattern}/" for pattern in patterns]


def file_signature(root: Path, path: str) -> tuple[int, ...] | None:
    """lstat each component, reject links/reparse points, and return regular-file metadata."""
    relative = PurePosixPath(path)
    if (
        relative.is_absolute()
        or ".." in relative.parts
        or str(relative) != path
        or not relative.parts
    ):
        raise InventoryError("Git returned an unsafe or non-normalized relative path.")
    current = root
    signature: list[int] = []
    for index, part in enumerate(relative.parts):
        current = current / part
        try:
            info = current.lstat()
        except FileNotFoundError, NotADirectoryError:
            return None
        # Windows junctions and cloud/other reparse points are conservatively excluded.
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            return None
        last = index == len(relative.parts) - 1
        if not last:
            if not stat.S_ISDIR(info.st_mode):
                return None
            # Recognize custom-named virtual environments without reading any content.
            try:
                environment_marker = (current / "pyvenv.cfg").lstat()
            except FileNotFoundError, NotADirectoryError:
                environment_marker = None
            if environment_marker is not None:
                return None
            signature.extend((info.st_dev, info.st_ino))
        elif stat.S_ISREG(info.st_mode):
            signature.extend(
                (
                    info.st_dev,
                    info.st_ino,
                    info.st_mode,
                    info.st_size,
                    info.st_mtime_ns,
                    info.st_ctime_ns,
                )
            )
        else:
            return None
    return tuple(signature)


def inspect_repository(supplied: str | Path, *, max_files: int = MAX_FILES) -> Inventory:
    """Gather and validate facts, rejecting observed state/eligibility changes during scanning."""
    if max_files < 1 or max_files > MAX_FILES:
        raise InventoryError(f"File limit must be between 1 and {MAX_FILES:,}.")
    root = repository_root(supplied)
    identity = git_identity(root)
    exclusions = git_exclusions()
    paths, links = git_paths(root, exclusions)
    signatures: dict[str, tuple[int, ...] | None] = {}
    files: list[FileEntry] = []
    for path, tracked in sorted(paths.items()):
        if path in links or any(
            parent.as_posix() in links for parent in PurePosixPath(path).parents
        ):
            continue
        if excluded_path(path):
            continue
        signature = file_signature(root, path)
        signatures[path] = signature
        if signature is None:
            continue
        if len(files) >= max_files:
            raise InventoryError(
                f"Inventory exceeds the {max_files:,} file limit; no partial inventory returned."
            )
        files.append(
            FileEntry(path=path, extension=PurePosixPath(path).suffix.lower(), tracked=tracked)
        )
    if git_identity(root) != identity or git_paths(root, exclusions) != (paths, links):
        raise InventoryError("Repository state changed during inspection; retry when it is stable.")
    if any(file_signature(root, path) != signature for path, signature in signatures.items()):
        raise InventoryError(
            "File eligibility or metadata changed during inspection; retry when it is stable."
        )
    tracked_count = sum(entry.tracked for entry in files)
    return Inventory(
        project_name=root.name,
        git=identity,
        files=files,
        counts=FileCounts(
            total_files=len(files),
            tracked_files=tracked_count,
            untracked_files=len(files) - tracked_count,
        ),
    )
