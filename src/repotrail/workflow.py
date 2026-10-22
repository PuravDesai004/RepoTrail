"""Real conditional LangGraph workflow, durable attempt charges and SQLite resume."""

import json
import logging
import re
import sqlite3
import time
from pathlib import Path
from typing import Any, Literal, TypedDict
from uuid import uuid4

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langsmith import tracing_context
from pydantic import ValidationError

from .ai_models import (
    AIConfigurationError,
    EvidencePackage,
    GenerationConfig,
    NodeEvent,
    Task,
    WorkflowResult,
    output_model,
)
from .git import InventoryError, repository_root
from .inventory import excluded_path
from .product_models import stable_digest
from .provider import ModelGateway, build_messages, create_model
from .selection import collect_evidence, repository_id, source_text
from .store import AttemptBudgetExhausted, StateStore, default_state_dir


class RunState(TypedDict, total=False):
    run_id: str
    request: dict[str, Any]
    package: dict[str, Any]
    cache_key: str
    cache_status: Literal["hit", "miss", "invalid", "not_checked"]
    candidate: dict[str, Any] | None
    answer: dict[str, Any] | None
    errors: list[str]
    fatal_error: str | None
    usage: list[dict[str, Any]]
    used_ids: list[str]
    source_bytes: int
    metadata_bytes: int


def validate_answer(
    task: Task, candidate: dict[str, Any], package: EvidencePackage
) -> tuple[dict, list[str]]:
    model = output_model(task).model_validate(candidate)
    payload = model.model_dump(mode="json")
    registry = {source.reference.id: source for source in package.sources}
    used = set()
    for values in payload.values():
        if isinstance(values, list):
            for record in values:
                if isinstance(record, dict) and "citations" in record:
                    for identifier in record["citations"]:
                        if identifier not in registry:
                            raise ValueError("unsupported_citation")
                        # Revalidate paths/hash/revision/ranges against retained source, not model strings.
                        type(registry[identifier]).model_validate(registry[identifier].model_dump())
                        used.add(identifier)
    return payload, sorted(used)


class WorkflowService:
    def __init__(
        self,
        supplied: str | Path,
        *,
        config: GenerationConfig | None = None,
        state_dir: Path | None = None,
        model: ModelGateway | None = None,
    ):
        self.root = repository_root(supplied)
        self.config = config or GenerationConfig()
        directory = (state_dir or default_state_dir()).resolve()
        if directory.is_relative_to(self.root) and not excluded_path(
            (directory / "cache.sqlite3").relative_to(self.root).as_posix()
        ):
            raise AIConfigurationError(
                "State must be outside the inspected repository or intentionally configured in an excluded directory such as .local."
            )
        self.store = StateStore(directory)
        self.model = model
        self.backend = (
            model.backend_id if model is not None else f"langchain-{self.config.provider}"
        )
        self.connection = sqlite3.connect(
            self.store.checkpoint_path, check_same_thread=False, timeout=5
        )
        self.saver = SqliteSaver(self.connection, serde=JsonPlusSerializer(pickle_fallback=False))

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.connection.close()

    def configuration_fingerprint(self, task: Task) -> str:
        return stable_digest(
            {"task": task, "config": self.config.model_dump(mode="json"), "backend": self.backend}
        )

    def graph(self, *, pause_before_generate: bool = False):
        graph = StateGraph(RunState)
        for name, function in [
            ("collect", self.collect),
            ("select", self.select),
            ("cache", self.cache),
            ("generate", self.generate),
            ("validate", self.validate),
            ("repair", self.repair),
            ("persist", self.persist),
            ("failure", self.failure),
        ]:
            graph.add_node(name, self.timed(name, function))
        graph.add_edge(START, "collect")
        graph.add_conditional_edges(
            "collect", lambda state: "failure" if state.get("fatal_error") else "select"
        )
        graph.add_edge("select", "cache")
        graph.add_conditional_edges(
            "cache", lambda state: "persist" if state.get("answer") is not None else "generate"
        )
        graph.add_edge("generate", "validate")
        graph.add_conditional_edges("validate", self.validation_route)
        graph.add_edge("repair", "generate")
        graph.add_edge("persist", END)
        graph.add_edge("failure", END)
        return graph.compile(
            checkpointer=self.saver,
            interrupt_before=["generate"] if pause_before_generate else None,
        )

    def timed(self, name: str, function):
        def node(state: RunState):
            start = time.perf_counter()
            details = {"status": "ok"}
            try:
                update = function(state)
                if update.get("fatal_error"):
                    details["status"] = "failed"
                return update
            except BaseException:
                details["status"] = "raised"
                raise
            finally:
                elapsed = (time.perf_counter() - start) * 1000
                self.store.event(state["run_id"], name, elapsed, details)
                logging.getLogger(__name__).info(
                    "workflow_node",
                    extra={"run_id": state["run_id"], "node": name, "elapsed_ms": elapsed},
                )

        return node

    def collect(self, state: RunState) -> RunState:
        request = state["request"]
        try:
            package = collect_evidence(
                self.root,
                request["task"],
                self.config,
                request.get("base_ref"),
                request.get("head_ref"),
            )
            payload = package.model_dump(mode="json")
            self.store.set_evidence(state["run_id"], stable_digest(payload), payload)
            return {"package": payload, "errors": [], "fatal_error": None}
        except (InventoryError, AIConfigurationError) as exc:
            return {"fatal_error": "evidence_error", "errors": [str(exc)]}

    def select(self, state: RunState) -> RunState:
        package = EvidencePackage.model_validate(state["package"])
        key = stable_digest(
            {
                "repository_id": package.repository_id,
                "evidence": stable_digest(state["package"]),
                "configuration": self.configuration_fingerprint(package.task),
            }
        )
        return {
            "cache_key": key,
            "cache_status": "miss",
            "source_bytes": len(source_text(package.sources).encode()),
            "metadata_bytes": len(json.dumps(package.metadata, ensure_ascii=True).encode()),
        }

    def cache(self, state: RunState) -> RunState:
        cached = self.store.get_cache(state["cache_key"])
        if cached is None:
            return {}
        package = EvidencePackage.model_validate(state["package"])
        try:
            answer, ids = validate_answer(package.task, json.loads(cached), package)
            return {"answer": answer, "used_ids": ids, "cache_status": "hit"}
        except ValueError, ValidationError, TypeError:
            self.store.delete_cache(state["cache_key"])
            return {"cache_status": "invalid"}

    def generate(self, state: RunState) -> RunState:
        package = EvidencePackage.model_validate(state["package"])
        try:
            messages, source_bytes, metadata_bytes = build_messages(
                package, self.config, state.get("errors", []), state.get("candidate")
            )
            if self.model is None:
                self.model = create_model(self.config)
        except AIConfigurationError as exc:
            return {
                "fatal_error": "configuration_or_input_budget",
                "errors": [str(exc)],
                "candidate": None,
            }
        try:
            attempt = self.store.charge_attempt(state["run_id"], self.config.max_attempts)
        except AttemptBudgetExhausted:
            return {
                "fatal_error": "attempt_budget_exhausted",
                "errors": ["Persisted provider-attempt budget exhausted."],
                "candidate": None,
            }
        reply = self.model.invoke(package.task, messages, self.config)
        usage = [
            *state.get("usage", []),
            {"attempt": attempt, "backend": self.backend, **reply.usage},
        ]
        return {
            "candidate": reply.payload,
            "errors": [reply.error_code] if reply.error_code else [],
            "fatal_error": reply.error_code if reply.error_code and not reply.retryable else None,
            "usage": usage,
            "source_bytes": source_bytes,
            "metadata_bytes": metadata_bytes,
        }

    def validate(self, state: RunState) -> RunState:
        candidate = state.get("candidate")
        if candidate is None:
            return {"errors": state.get("errors") or ["invalid_provider_output"]}
        try:
            answer, ids = validate_answer(
                state["request"]["task"],
                candidate,
                EvidencePackage.model_validate(state["package"]),
            )
            return {"answer": answer, "used_ids": ids, "errors": [], "fatal_error": None}
        except ValueError, ValidationError, TypeError:
            return {
                "errors": [
                    "Output schema/citations/grounding fields are invalid; use registered IDs and the requested schema."
                ]
            }

    def validation_route(self, state: RunState) -> str:
        if state.get("answer") is not None:
            return "persist"
        row = self.store.get_run(state["run_id"])
        if state.get("fatal_error") or row is None or row["attempts"] >= self.config.max_attempts:
            return "failure"
        return "repair"

    def repair(self, state: RunState) -> RunState:
        return {"errors": state.get("errors") or ["Repair the invalid structured answer once."]}

    def persist(self, state: RunState) -> RunState:
        answer = state.get("answer")
        if answer is not None:
            package = EvidencePackage.model_validate(state["package"])
            self.store.put_cache(state["cache_key"], package.repository_id, answer)
        return {}

    def failure(self, state: RunState) -> RunState:
        return {
            "errors": state.get("errors")
            or ["The bounded workflow could not produce a validated answer."]
        }

    def result(self, state: RunState, *, interrupted: bool = False) -> WorkflowResult:
        row = self.store.get_run(state["run_id"])
        assert row is not None
        package = EvidencePackage.model_validate(state["package"]) if state.get("package") else None
        registry = (
            {source.reference.id: source.reference for source in package.sources} if package else {}
        )
        valid = state.get("answer") is not None
        status: Literal["completed", "incomplete", "failed", "interrupted", "stale"]
        if interrupted:
            status = "interrupted"
        elif valid:
            status = "completed" if package and package.complete else "incomplete"
        else:
            status = "failed"
        result = WorkflowResult(
            run_id=state["run_id"],
            task=state["request"]["task"],
            status=status,
            answer=state.get("answer"),
            citations=[registry[identifier] for identifier in state.get("used_ids", [])],
            evidence_complete=package.complete if package else False,
            omissions=package.omissions if package else [],
            evidence_diagnostics=package.diagnostics if package else [],
            errors=state.get("errors", []),
            provider=self.config.provider,
            model=self.config.model,
            backend=self.backend,
            attempts=row["attempts"],
            cache_status=state.get("cache_status", "not_checked"),
            selected_source_bytes=state.get("source_bytes", 0),
            selected_metadata_bytes=state.get("metadata_bytes", 0),
            usage=state.get("usage", []),
            node_events=[
                NodeEvent.model_validate(event) for event in self.store.events(state["run_id"])
            ],
        )
        self.store.finish_run(state["run_id"], result.status, result.model_dump(mode="json"))
        return result

    def run(
        self,
        task: Task,
        *,
        base_ref: str | None = None,
        head_ref: str | None = None,
        run_id: str | None = None,
        pause_before_generate: bool = False,
    ) -> WorkflowResult:
        identifier = run_id or uuid4().hex
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", identifier):
            raise AIConfigurationError(
                "Run ID must contain 1..80 letters, digits, underscores, or hyphens."
            )
        request = {"task": task, "base_ref": base_ref, "head_ref": head_ref}
        self.store.create_run(
            identifier,
            repository_id(self.root),
            "",
            self.configuration_fingerprint(task),
            request,
            {},
        )
        graph = self.graph(pause_before_generate=pause_before_generate)
        config = {"configurable": {"thread_id": identifier}, "recursion_limit": 24}
        with tracing_context(enabled=False):
            state = graph.invoke({"run_id": identifier, "request": request, "usage": []}, config)
        return self.result(state, interrupted=bool(graph.get_state(config).next))

    def resume(self, run_id: str) -> WorkflowResult:
        row = self.store.get_run(run_id)
        if row is None:
            raise AIConfigurationError("Run ID was not found in this state directory.")
        request = json.loads(row["request_json"])
        package = collect_evidence(
            self.root,
            request["task"],
            self.config,
            request.get("base_ref"),
            request.get("head_ref"),
        )
        if (
            row["repository_id"] != package.repository_id
            or row["evidence_fingerprint"] != stable_digest(package.model_dump(mode="json"))
            or row["config_fingerprint"] != self.configuration_fingerprint(request["task"])
        ):
            return WorkflowResult(
                run_id=run_id,
                task=request["task"],
                status="stale",
                evidence_complete=False,
                omissions=[],
                evidence_diagnostics=[],
                errors=["Run repository/evidence/configuration changed; start a new run."],
                provider=self.config.provider,
                model=self.config.model,
                backend=self.backend,
                attempts=row["attempts"],
                cache_status="not_checked",
                selected_source_bytes=0,
                selected_metadata_bytes=0,
            )
        graph = self.graph()
        config = {"configurable": {"thread_id": run_id}, "recursion_limit": 24}
        snapshot = graph.get_state(config)
        if not snapshot.values:
            raise AIConfigurationError(
                "Run has no resumable checkpoint; start a new run without resetting this run's charges."
            )
        with tracing_context(enabled=False):
            state = graph.invoke(None, config) if snapshot.next else snapshot.values
        return self.result(state, interrupted=bool(graph.get_state(config).next))

    def prune(self, keep: int = 100) -> int:
        deleted = self.store.prune(keep)
        for identifier in deleted:
            self.saver.delete_thread(identifier)
        return len(deleted)
