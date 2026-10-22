"""Run deterministic fixture metrics and worked workflow lifecycles without a key."""

import argparse
import json
import tempfile
from pathlib import Path

from repotrail.demo import build_demo
from repotrail.evidence import get_project_overview, get_symbol_context
from repotrail.history import compare_revisions
from repotrail.provider import ModelReply
from repotrail.workflow import WorkflowService, validate_answer


class EvaluationModel:
    backend_id = "scripted-evaluation-only"

    def __init__(self, actions=None):
        self.actions = list(actions or [])
        self.calls = 0

    def invoke(self, task, messages, config):
        self.calls += 1
        action = self.actions.pop(0) if self.actions else "valid"
        data = json.loads(messages[1].content.split("TASK DATA:\n")[1].split("\nSOURCE DATA:\n")[0])
        references = data["references"]
        reference = next(ref for ref in references if ref["path"] == "pricing.py")
        identifier = reference["id"] if action == "valid" else "src:unsupported"
        claim = {
            "text": "total_price uses sum(prices) - discount.",
            "basis": "observed",
            "citations": [identifier],
        }
        if task == "explain_project":
            packaging = next(ref for ref in references if ref["path"] == "pyproject.toml")
            payload = {
                "summary": [claim],
                "structure": [],
                "entry_points": [
                    {
                        "text": "tiny-shop declares pricing:main.",
                        "basis": "observed",
                        "citations": [packaging["id"]],
                    }
                ],
                "selected_flows": [],
                "unknowns": ["Source was not executed; runtime import resolution is unknown."],
            }
        elif task == "explain_changes":
            payload = {
                "observed_edits": [claim],
                "inferred_consequences": [],
                "unknowns": ["Endpoint trees do not prove complete causation."],
            }
        else:
            ids = [ref["id"] for ref in references if ref["path"] == "pricing.py"]
            payload = {
                "observations": [claim],
                "findings": [
                    {
                        "severity": "medium",
                        "issue": "A fractional discount is subtracted as an amount",
                        "citations": ids,
                        "reasoning": "The docstring defines a fraction; the changed formula subtracts the supplied value directly.",
                        "suggested_validation": "Check [10, 20] with 0.1: fractional interpretation expects 27, changed formula yields 29.9",
                        "basis": "inferred",
                    }
                ],
                "unknowns": ["Finding is an inference, not an executed test."],
            }
        return ModelReply(payload, usage={"reported": False, "estimated": False})


def evaluate() -> dict:
    cases = json.loads(Path(__file__).with_name("cases.json").read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(prefix="repotrail-eval-") as temporary:
        directory = Path(temporary)
        root = directory / "synthetic"
        base, head = build_demo(root)
        overview = get_project_overview(root)
        context = get_symbol_context(root, "pricing.py", "total_price")
        changes = compare_revisions(root, base, head)
        facts = [
            overview.packaging.name,
            overview.packaging.scripts["tiny-shop"],
            context.qualified_name,
        ]
        covered = sum(fact in facts for fact in cases["structure"]["required_facts"])
        assert covered == 3
        assert all(
            path in {file.old_path or file.new_path for file in changes.files}
            for path in cases["changes"]["required_paths"]
        )
        assert changes.rename_policy == cases["changes"]["required_facts"][1]
        model = EvaluationModel()
        results = []
        with WorkflowService(root, state_dir=directory / "state", model=model) as service:
            for task in ("explain_project", "explain_changes", "review_diff"):
                result = service.run(task, base_ref=base, head_ref=head)
                assert result.answer is not None
                results.append(result)
            before = model.calls
            cached = service.run("explain_project", base_ref=base, head_ref=head)
            assert cached.cache_status == "hit" and model.calls == before
            (root / "pricing.py").write_bytes(
                (root / "pricing.py").read_bytes() + b"\n# evaluation invalidation\n"
            )
            changed = service.run("explain_project", base_ref=base, head_ref=head)
            assert changed.cache_status == "miss" and model.calls == before + 1
        with WorkflowService(root, state_dir=directory / "resume", model=model) as service:
            paused = service.run(
                "explain_project", run_id="evaluation-resume", pause_before_generate=True
            )
            assert paused.status == "interrupted" and paused.attempts == 0
        with WorkflowService(root, state_dir=directory / "resume", model=model) as recreated:
            resumed = recreated.resume("evaluation-resume")
            assert resumed.answer is not None
        with WorkflowService(
            root, state_dir=directory / "repair", model=EvaluationModel(["bad", "valid"])
        ) as service:
            repaired = service.run("explain_project")
            assert repaired.answer and repaired.attempts == 2
        with WorkflowService(
            root, state_dir=directory / "failure", model=EvaluationModel(["bad", "bad"])
        ) as service:
            failed = service.run("explain_project")
            assert failed.status == "failed" and failed.attempts == 2
        accepted = 0
        resolved = 0
        citations = 0
        rejected = 0
        for result in results:
            # Project evidence was edited; validate its retained pre-edit package from the run DB.
            with WorkflowService(root, state_dir=directory / "state", model=model) as service:
                row = service.store.get_run(result.run_id)
            from repotrail.ai_models import EvidencePackage

            package = EvidencePackage.model_validate_json(row["package_json"])
            _, identifiers = validate_answer(result.task, result.answer, package)
            accepted += 1
            citations += len(result.citations)
            resolved += len(identifiers)
            malformed = json.loads(json.dumps(result.answer))
            key = (
                "summary"
                if result.task == "explain_project"
                else "observed_edits"
                if result.task == "explain_changes"
                else "observations"
            )
            malformed[key][0]["citations"] = ["src:unsupported"]
            try:
                validate_answer(result.task, malformed, package)
            except ValueError:
                rejected += 1
        return {
            "backend": model.backend_id,
            "semantic_accuracy_measured": False,
            "metrics": {
                "schema_accepted": {"passed": accepted, "total": 3},
                "citations_resolved": {"passed": resolved, "total": citations},
                "required_structure_facts": {"passed": covered, "total": 3},
                "unsupported_reference_rejected": {"passed": rejected, "total": 3},
                "cache_hit_extra_calls": 0,
                "unstaged_edit_invalidation_extra_calls": 1,
                "maximum_attempts_observed": max(
                    result.attempts for result in [*results, repaired, failed]
                ),
                "attempt_limit": 2,
            },
            "lifecycles": {
                "overview": overview.model_dump(mode="json"),
                "context": context.model_dump(mode="json"),
                "changes": changes.model_dump(mode="json"),
                "ai": [result.model_dump(mode="json") for result in results],
                "cache": cached.model_dump(mode="json"),
                "invalidated": changed.model_dump(mode="json"),
                "repaired": repaired.model_dump(mode="json"),
                "failed": failed.model_dump(mode="json"),
                "paused": paused.model_dump(mode="json"),
                "resumed": resumed.model_dump(mode="json"),
            },
        }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = evaluate()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    print(json.dumps(report["metrics"], indent=2))


if __name__ == "__main__":
    main()
