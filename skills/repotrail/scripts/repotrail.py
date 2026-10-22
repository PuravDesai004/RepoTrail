"""Portable native-agent launcher. Setup installs only the bundled trusted engine."""

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from bundle_support import clean_environment, default_cache, ensure_runtime, target_root  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=str(Path.cwd()))
    parser.add_argument("--mode", choices=["commit", "whole", "source"], default="commit")
    parser.add_argument("--ref")
    parser.add_argument("--cursor")
    parser.add_argument("--page-size", type=int, default=10)
    parser.add_argument("--path")
    parser.add_argument("--start-line", type=int, default=1)
    parser.add_argument("--end-line", type=int)
    parser.add_argument("--cache-dir", type=Path, default=default_cache())
    parser.add_argument("--setup-only", action="store_true")
    parser.add_argument(
        "--api", action="store_true", help="explicit optional LangGraph/provider mode"
    )
    parser.add_argument("--provider", choices=["groq", "gemini"])
    parser.add_argument("--model", help="supported model ID for explicit API mode")
    parser.add_argument(
        "--task",
        choices=["explain_project", "explain_changes", "review_diff"],
        default="explain_project",
    )
    parser.add_argument("--base-ref")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    try:
        try:
            repository = target_root(args.repo)
        except ValueError, OSError:
            if not args.setup_only:
                raise
            repository = None
        if args.api and not args.provider:
            raise ValueError("Optional API mode requires an explicit --provider.")
        python, reused = ensure_runtime(root, args.cache_dir, repository, api=args.api)
        if args.setup_only:
            print(
                json.dumps(
                    {"runtime_python": str(python), "reused": reused, "api_enabled": args.api}
                )
            )
            return 0
        command = [str(python), "-I", "-m", "repotrail"]
        if args.api:
            command += [
                "explain",
                str(repository),
                "--provider",
                args.provider,
                "--task",
                args.task,
            ]
            if args.model:
                command += ["--model", args.model]
            if args.base_ref:
                command += ["--base-ref", args.base_ref]
            if args.ref:
                command += ["--head-ref", args.ref]
        else:
            command += [
                "agent",
                str(repository),
                "--mode",
                args.mode,
                "--page-size",
                str(args.page_size),
            ]
            for name, value in (
                ("--ref", args.ref),
                ("--cursor", args.cursor),
                ("--path", args.path),
                ("--start-line", args.start_line),
                ("--end-line", args.end_line),
            ):
                if value is not None:
                    command += [name, str(value)]
        command.append("--json")
        # CWD is the installed runtime, never the inspected repository.
        result = subprocess.run(
            command, cwd=python.parent, env=clean_environment(credentials=args.api), timeout=180
        )
        return result.returncode
    except ValueError, OSError, KeyError, TypeError, subprocess.TimeoutExpired:
        print(
            "RepoTrail skill setup or evidence request failed. Check the local repository root, Python3.14/Git, bundle integrity, cache permissions and network access for first setup. No inspected code is executed.",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
