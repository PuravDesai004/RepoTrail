"""Install this verified skill folder for Codex or Claude Code; no config edits."""

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent / "scripts"))
from bundle_support import bundle_files  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", choices=["codex", "claude"], required=True)
    parser.add_argument(
        "--destination", type=Path, help="override the host's skills parent directory"
    )
    args = parser.parse_args()
    source = Path(__file__).resolve().parent
    try:
        manifest, paths = bundle_files(source)
        parent = args.destination or Path.home() / (
            ".agents/skills" if args.host == "codex" else ".claude/skills"
        )
        parent = parent.expanduser().resolve()
        target = parent / "repotrail"
        if target.exists():
            existing, _ = bundle_files(target)
            if existing != manifest:
                raise ValueError(
                    "A different skill already exists. Back it up or select another destination before upgrading."
                )
        else:
            parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix="repotrail-install-", dir=parent) as temporary:
                staged = Path(temporary) / "repotrail"
                staged.mkdir()
                for relative in [*paths, "BUNDLE_MANIFEST.json"]:
                    destination = staged / relative
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source / relative, destination)
                bundle_files(staged)
                if not staged.resolve().is_relative_to(
                    parent
                ) or not target.resolve().is_relative_to(parent):
                    raise ValueError("Install target escaped the requested skills directory.")
                staged.rename(target)
        print(
            json.dumps(
                {
                    "installed": str(target),
                    "host": args.host,
                    "invoke": "$repotrail" if args.host == "codex" else "/repotrail",
                    "mode": "current_agent_model",
                    "api_key_required": False,
                }
            )
        )
        return 0
    except OSError, ValueError, KeyError:
        print(
            "Skill installation failed: verify the complete bundle, permissions, and destination. Existing skills are preserved.",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
