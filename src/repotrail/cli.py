"""CLI boundary: lazy optional integrations, validated documents, stable exit codes."""

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from .ai_models import AIConfigurationError, GenerationConfig
from .analysis import analyze_python_repository
from .evidence import get_project_overview, get_symbol_context
from .git import InventoryError
from .history import compare_revisions, recent_history
from .inventory import MAX_FILES, inspect_repository
from .telemetry import configure_logging


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="repotrail")
    commands = parser.add_subparsers(dest="command", required=True)
    for name, help_text in {
        "inspect": "inventory the current Git working tree",
        "symbols": "map Python declarations and imports",
        "overview": "observe structure, README, packaging and local history",
        "context": "retrieve exact verified declaration source",
        "history": "read bounded local commit metadata",
        "changes": "compare two committed endpoint trees",
        "explain": "explicitly request a grounded Gemini analysis",
        "resume": "resume a persistent analysis run",
        "cache-prune": "prune old local analysis state",
    }.items():
        command = commands.add_parser(name, help=help_text)
        command.add_argument("repository_root")
        command.add_argument("--json", action="store_true", help="print validated JSON")
        if name == "inspect":
            command.add_argument("--max-files", type=int, default=MAX_FILES)
        if name == "context":
            command.add_argument("path")
            command.add_argument("qualified_name")
            command.add_argument("--definition-line", type=int)
        if name == "history":
            command.add_argument("--ref", default="HEAD")
            command.add_argument("--limit", type=int, default=20)
        if name == "changes":
            command.add_argument("base_ref")
            command.add_argument("head_ref")
        if name in ("explain", "resume", "cache-prune"):
            command.add_argument("--state-dir", type=Path)
            command.add_argument("--model", default="gemini-3.8-flash")
        if name == "explain":
            command.add_argument(
                "--task",
                choices=["explain_project", "explain_changes", "review_diff"],
                default="explain_project",
            )
            command.add_argument("--base-ref")
            command.add_argument("--head-ref")
            command.add_argument("--run-id")
            command.add_argument("--pause-before-generate", action="store_true")
            command.add_argument("--max-attempts", type=int, default=2, choices=[1, 2])
        if name == "resume":
            command.add_argument("run_id")
            command.add_argument("--max-attempts", type=int, default=2, choices=[1, 2])
        if name == "cache-prune":
            command.add_argument("--keep", type=int, default=100)
    demo = commands.add_parser("demo", help="create a new isolated synthetic Git fixture")
    demo.add_argument("destination", type=Path)
    demo.add_argument("--json", action="store_true")
    mcp = commands.add_parser("mcp", help="serve a single repository over MCP stdio")
    mcp.add_argument("--repo", required=True)
    mcp.add_argument("--enable-ai", action="store_true")
    mcp.add_argument("--state-dir", type=Path)
    mcp.add_argument("--model", default="gemini-3.8-flash")
    return parser


def render_human(value, level: int = 0) -> str:
    """Render every field from the same result, rather than a second partial schema."""
    indent = "  " * level
    if isinstance(value, dict):
        return "\n".join(
            f"{indent}{key}:"
            + (
                "\n" + render_human(item, level + 1)
                if isinstance(item, (dict, list)) and item
                else " " + json.dumps(item, ensure_ascii=True)
            )
            for key, item in value.items()
        )
    if isinstance(value, list):
        return "\n".join(
            f"{indent}-\n{render_human(item, level + 1)}"
            if isinstance(item, (dict, list))
            else f"{indent}- {json.dumps(item, ensure_ascii=True)}"
            for item in value
        )
    return indent + json.dumps(value, ensure_ascii=True)


def execute(args):
    root = getattr(args, "repository_root", "")
    result: Any
    if args.command == "inspect":
        return inspect_repository(root, max_files=args.max_files), 0
    if args.command == "symbols":
        result = analyze_python_repository(root)
    elif args.command == "overview":
        result = get_project_overview(root)
    elif args.command == "context":
        result = get_symbol_context(root, args.path, args.qualified_name, args.definition_line)
    elif args.command == "history":
        result = recent_history(root, ref=args.ref, limit=args.limit)
    elif args.command == "changes":
        result = compare_revisions(root, args.base_ref, args.head_ref)
    elif args.command == "demo":
        from .demo import build_demo

        base, head = build_demo(args.destination)
        return {"repository": str(args.destination.resolve()), "base": base, "head": head}, 0
    elif args.command == "mcp":
        try:
            from .mcp_server import create_server
        except ImportError as exc:
            raise AIConfigurationError(
                "Install the MCP extra: pip install 'repotrail[mcp]'."
            ) from exc
        create_server(
            args.repo,
            enable_ai=args.enable_ai,
            state_dir=args.state_dir,
            config=GenerationConfig(model=args.model),
        ).run(transport="stdio")
        return None, 0
    else:
        try:
            from .workflow import WorkflowService
        except ImportError as exc:
            raise AIConfigurationError(
                "Install the AI extra: pip install 'repotrail[ai]'."
            ) from exc
        configuration = GenerationConfig(
            model=args.model, max_attempts=getattr(args, "max_attempts", 2)
        )
        with WorkflowService(root, config=configuration, state_dir=args.state_dir) as service:
            if args.command == "cache-prune":
                return {"deleted_runs": service.prune(args.keep), "keep": args.keep}, 0
            if args.command == "resume":
                result = service.resume(args.run_id)
            else:
                result = service.run(
                    args.task,
                    base_ref=args.base_ref,
                    head_ref=args.head_ref,
                    run_id=args.run_id,
                    pause_before_generate=args.pause_before_generate,
                )
        return result, 0 if result.status == "completed" else 1
    return result, 0 if result.complete else 1


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    args = build_parser().parse_args(argv)
    try:
        result, exit_code = execute(args)
        if result is not None:
            payload = result.model_dump(mode="json") if isinstance(result, BaseModel) else result
            print(
                json.dumps(payload, indent=2, ensure_ascii=True)
                if args.json
                else render_human(payload)
            )
        return exit_code
    except (
        InventoryError,
        AIConfigurationError,
        OSError,
        ValueError,
        ValidationError,
        sqlite3.Error,
    ) as exc:
        message = (
            str(exc)
            if isinstance(exc, (InventoryError, AIConfigurationError))
            else "Operation failed: inaccessible files or invalid request/repository metadata."
        )
        print(f"repotrail: {message}", file=sys.stderr)
        return 2
