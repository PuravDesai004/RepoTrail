"""Create a synthetic disposable history for examples, tests, and opt-in live checks."""

import os
import subprocess
from pathlib import Path

from .git import InventoryError


def fixture_git(root: Path, *args: str) -> str:
    # Keep fixture writes in their explicit destination despite inherited Git settings.
    environment = {
        key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")
    }
    environment.update(
        GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1", GIT_TERMINAL_PROMPT="0"
    )
    result = subprocess.run(
        [
            "git",
            "-c",
            "init.templateDir=",
            "-c",
            "core.hooksPath=",
            "-c",
            "core.fsmonitor=false",
            "-c",
            "user.name=RepoTrail Demo",
            "-c",
            "user.email=demo@example.invalid",
            *args,
        ],
        cwd=root,
        env=environment,
        capture_output=True,
        timeout=10,
        check=False,
    )
    if result.returncode:
        raise InventoryError("Synthetic fixture Git setup failed.")
    return result.stdout.decode("utf-8").strip()


def build_demo(destination: Path) -> tuple[str, str]:
    """Require an empty directory; fixture commits affect only that explicit destination."""
    destination.mkdir(parents=True, exist_ok=True)
    if any(destination.iterdir()):
        raise InventoryError(
            "Synthetic demo destination must be empty; existing work is preserved."
        )
    fixture_git(destination, "init", "-b", "main")
    files = {
        ".gitignore": ".local/\n__pycache__/\n",
        "README.md": "# Tiny Shop\nA demo only. discount is a fraction between zero and one.\nRun the declared tiny-shop entry point.\n",
        "pyproject.toml": '[project]\nname="tiny-shop"\nversion="0.0.1"\ndependencies=[]\n[project.scripts]\ntiny-shop="pricing:main"\n',
        "pricing.py": 'def total_price(prices, discount=0.0):\n    """discount is a fraction from zero to one."""\n    return sum(prices) * (1 - discount)\n\ndef main():\n    return total_price([10, 20], 0.1)\n',
        "notes old.txt": "Synthetic notes only.\n",
        ".env": "SYNTHETIC_PLACEHOLDER=not-a-real-key\n",
    }
    for path, content in files.items():
        (destination / path).write_bytes(content.encode("utf-8"))
    (destination / "asset.bin").write_bytes(b"\0OLD")
    fixture_git(destination, "add", ".")
    fixture_git(destination, "commit", "-m", "Synthetic fractional discount baseline")
    base = fixture_git(destination, "rev-parse", "HEAD")
    (destination / "pricing.py").write_bytes(
        files["pricing.py"]
        .replace("sum(prices) * (1 - discount)", "sum(prices) - discount")
        .encode()
    )
    (destination / "notes old.txt").rename(destination / "notes new Ω.txt")
    (destination / "added module.py").write_bytes(b"def helper():\n    return 1\n")
    (destination / "asset.bin").write_bytes(b"\0NEW")
    (destination / ".env").write_bytes(b"SYNTHETIC_PLACEHOLDER=still-not-a-real-key\n")
    fixture_git(destination, "add", "-A")
    fixture_git(destination, "commit", "-m", "Synthetic change: subtract discount directly")
    head = fixture_git(destination, "rev-parse", "HEAD")
    # Deliberately dirty, untracked evidence is distinct from the committed comparison.
    (destination / "working only.py").write_bytes(b"def unstaged_helper():\n    return 3\n")
    (destination / "pricing.py").write_bytes(
        (destination / "pricing.py").read_bytes() + b"\n# Unstaged working-tree note.\n"
    )
    return base, head
