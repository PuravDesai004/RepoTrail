"""Explicit synthetic provider smoke with a durable three-attempt ceiling per directory.

This script spends provider quota only with --live. It never runs in offline CI.
"""

import argparse
import json
import sqlite3
from pathlib import Path

from repotrail.ai_models import DEFAULT_MODELS, GenerationConfig
from repotrail.demo import build_demo
from repotrail.provider import ModelReply, create_model
from repotrail.workflow import WorkflowService


class SmokeBudgetGateway:
    def __init__(self, directory: Path, configuration: GenerationConfig):
        self.directory = directory
        self.configuration = configuration
        self.backend_id = f"langchain-{configuration.provider}"
        self.path = directory / "live_budget.sqlite3"
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS charges (id INTEGER PRIMARY KEY, task TEXT NOT NULL)"
            )

    def invoke(self, task, messages, config):
        with sqlite3.connect(self.path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute("SELECT COUNT(*) FROM charges").fetchone()[0] >= 3:
                return ModelReply(None, "smoke_live_budget_exhausted", retryable=False)
            connection.execute("INSERT INTO charges(task) VALUES (?)", (task,))
        # A charge is durable before even constructing the network-capable adapter.
        return create_model(self.configuration).invoke(task, messages, config)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", required=True)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--provider", choices=["gemini", "groq"], default="gemini")
    parser.add_argument("--model")
    parser.add_argument(
        "--task", choices=["explain_project", "review_diff", "both"], default="both"
    )
    args = parser.parse_args()
    directory = args.directory.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    fixture = directory / "synthetic"
    marker = directory / "fixture_refs.json"
    if marker.exists():
        refs = json.loads(marker.read_text())
        base, head = refs["base"], refs["head"]
    else:
        base, head = build_demo(fixture)
        marker.write_text(json.dumps({"base": base, "head": head}), encoding="utf-8")
    configuration = GenerationConfig(
        provider=args.provider, model=args.model or DEFAULT_MODELS[args.provider], max_attempts=1
    )
    gateway = SmokeBudgetGateway(directory, configuration)
    results = []
    with WorkflowService(
        fixture, config=configuration, state_dir=directory / "state", model=gateway
    ) as service:
        tasks = ("explain_project", "review_diff") if args.task == "both" else (args.task,)
        for task in tasks:
            results.append(service.run(task, base_ref=base, head_ref=head).model_dump(mode="json"))
    with sqlite3.connect(gateway.path) as connection:
        charges = connection.execute("SELECT COUNT(*) FROM charges").fetchone()[0]
    report = {
        "synthetic_only": True,
        "smoke_live_charges": charges,
        "smoke_limit": 3,
        "model": configuration.model,
        "provider": configuration.provider,
        "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "smoke_live_charges": charges,
                "results": [
                    {
                        "task": result["task"],
                        "status": result["status"],
                        "attempts": result["attempts"],
                        "errors": result["errors"],
                        "usage": result["usage"],
                    }
                    for result in results
                ],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
