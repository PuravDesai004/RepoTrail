"""Offline tests exercise the compiled graph and real on-disk SQLite checkpointer."""

import json
import sqlite3

import pytest

from repotrail.ai_models import AIConfigurationError, GenerationConfig
from repotrail.demo import build_demo
from repotrail.provider import ModelReply, build_messages
from repotrail.selection import collect_evidence
from repotrail.workflow import WorkflowService


class ScriptedModel:
    backend_id = "scripted-test-double"

    def __init__(self, actions=None):
        self.actions = list(actions or ["valid"])
        self.calls = 0

    def invoke(self, task, messages, config):
        self.calls += 1
        action = self.actions.pop(0) if self.actions else "valid"
        if isinstance(action, BaseException):
            raise action
        if isinstance(action, ModelReply):
            return action
        metadata = json.loads(
            messages[1].content.split("TASK DATA:\n", 1)[1].split("\nSOURCE DATA:\n", 1)[0]
        )
        identifier = metadata["references"][0]["id"] if action != "bad_citation" else "src:unknown"
        claim = {
            "text": "The selected source declares total_price.",
            "basis": "observed",
            "citations": [identifier],
        }
        if action == "bad_schema":
            return ModelReply({"invented": True})
        if task == "explain_project":
            payload = {
                "summary": [claim],
                "structure": [],
                "entry_points": [],
                "selected_flows": [],
                "unknowns": ["Runtime behavior was not executed."],
            }
        elif task == "explain_changes":
            payload = {"observed_edits": [claim], "inferred_consequences": [], "unknowns": []}
        else:
            payload = {"observations": [claim], "findings": [], "unknowns": []}
        return ModelReply(payload, usage={"reported": False, "estimated": False})


@pytest.fixture
def demo(tmp_path):
    root = tmp_path / "repo"
    refs = build_demo(root)
    return root, {"base": refs[0], "head": refs[1]}, tmp_path / "state"


def test_graph_success_cache_zero_calls_and_corrupt_cache(demo):
    root, _, directory = demo
    model = ScriptedModel()
    with WorkflowService(root, state_dir=directory, model=model) as service:
        first = service.run("explain_project")
        assert first.answer and first.attempts == 1 and first.cache_status == "miss"
        assert [event.node for event in first.node_events] == [
            "collect",
            "select",
            "cache",
            "generate",
            "validate",
            "persist",
        ]
        second = service.run("explain_project")
        assert second.cache_status == "hit" and second.attempts == 0 and model.calls == 1
        with service.store.connection() as connection:
            connection.execute("UPDATE cache_entries SET answer_json=?", ('{"nonsense": 3}',))
        third = service.run("explain_project")
        assert third.cache_status == "invalid" and third.attempts == 1 and model.calls == 2
        assert first.backend == "scripted-test-double"
        assert service.store.path != service.store.checkpoint_path


@pytest.mark.parametrize("bad", ["bad_citation", "bad_schema"])
def test_real_conditional_repair_and_failure_at_bound(demo, bad):
    root, refs, directory = demo
    with WorkflowService(root, state_dir=directory, model=ScriptedModel([bad, "valid"])) as service:
        repaired = service.run("review_diff", base_ref=refs["base"], head_ref=refs["head"])
        assert repaired.answer is not None and repaired.attempts == 2
        assert [event.node for event in repaired.node_events].count("repair") == 1
    with WorkflowService(
        root, state_dir=directory / "other", model=ScriptedModel([bad, bad, "valid"])
    ) as service:
        failed = service.run("explain_project")
        assert failed.status == "failed" and failed.answer is None and failed.attempts == 2
        assert failed.node_events[-1].node == "failure"


def test_transient_and_fatal_outcomes_and_missing_key(demo, monkeypatch):
    root, _, directory = demo
    for index, code in enumerate(("timeout", "rate_limit")):
        with WorkflowService(
            root,
            state_dir=directory / str(index),
            model=ScriptedModel([ModelReply(None, code), "valid"]),
        ) as service:
            assert service.run("explain_project").attempts == 2
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    with WorkflowService(root, state_dir=directory / "missing") as service:
        result = service.run("explain_project")
        assert result.status == "failed" and result.attempts == 0
        assert "Missing Gemini credentials" in result.errors[0]
    with WorkflowService(
        root,
        state_dir=directory / "fatal",
        model=ScriptedModel([ModelReply(None, "provider_configuration", False)]),
    ) as service:
        result = service.run("explain_project")
        assert result.status == "failed" and result.attempts == 1


def test_cache_invalidation_source_config_repository_and_revisions(demo, tmp_path):
    root, refs, directory = demo
    model = ScriptedModel()
    with WorkflowService(root, state_dir=directory, model=model) as service:
        service.run("explain_project")
        (root / "pricing.py").write_text(
            (root / "pricing.py").read_text() + "\n# changed with unchanged HEAD\n"
        )
        assert service.run("explain_project").cache_status == "miss"
        service.run("explain_changes", base_ref=refs["base"], head_ref=refs["head"])
        assert (
            service.run(
                "explain_changes", base_ref=refs["head"], head_ref=refs["base"]
            ).cache_status
            == "miss"
        )
    for configuration in [
        GenerationConfig(prompt_version="test-2"),
        GenerationConfig(model="gemini-3.6-flash"),
    ]:
        with WorkflowService(
            root, state_dir=directory, model=model, config=configuration
        ) as service:
            assert service.run("explain_project").cache_status == "miss"
    other = tmp_path / "different-repo"
    build_demo(other)
    with WorkflowService(other, state_dir=directory, model=model) as service:
        assert service.run("explain_project").cache_status == "miss"


def test_checkpoint_recreated_resume_and_stale_rejection(demo):
    root, _, directory = demo
    with WorkflowService(root, state_dir=directory, model=ScriptedModel()) as service:
        paused = service.run("explain_project", run_id="resume-demo", pause_before_generate=True)
        assert paused.status == "interrupted" and paused.attempts == 0
    model = ScriptedModel()
    with WorkflowService(root, state_dir=directory, model=model) as recreated:
        result = recreated.resume("resume-demo")
        assert result.answer and result.attempts == 1 and model.calls == 1
        with recreated.store.connection() as connection:
            connection.execute("DELETE FROM cache_entries")
        pending = recreated.run("explain_project", run_id="stale-demo", pause_before_generate=True)
        assert pending.status == "interrupted"
    (root / "working only.py").write_text("def changed():\n    return 8\n")
    with WorkflowService(root, state_dir=directory, model=model) as recreated:
        assert recreated.resume("stale-demo").status == "stale"
        assert model.calls == 1


def test_charges_survive_crash_and_do_not_reset(demo):
    class Crash(BaseException):
        pass

    root, _, directory = demo
    configuration = GenerationConfig(max_attempts=1)
    with WorkflowService(
        root, state_dir=directory, config=configuration, model=ScriptedModel([Crash()])
    ) as service:
        with pytest.raises(Crash):
            service.run("explain_project", run_id="crashed")
        assert service.store.get_run("crashed")["attempts"] == 1
    model = ScriptedModel()
    with WorkflowService(root, state_dir=directory, config=configuration, model=model) as recreated:
        result = recreated.resume("crashed")
        assert result.status == "failed" and result.attempts == 1 and model.calls == 0
        with pytest.raises(AIConfigurationError, match="already exists"):
            recreated.run("explain_project", run_id="crashed")
        assert recreated.prune(0) == 1
        assert recreated.store.get_run("crashed") is None
        assert not recreated.graph().get_state({"configurable": {"thread_id": "crashed"}}).values


def test_prompt_and_reference_bounds_and_state_location(demo):
    root, _, directory = demo
    configuration = GenerationConfig(max_source_bytes=256, max_metadata_bytes=1024)
    package = collect_evidence(root, "explain_project", configuration)
    assert package.omissions and not package.complete
    with pytest.raises(AIConfigurationError, match="budget"):
        build_messages(package, configuration, [], None)
    with WorkflowService(
        root, state_dir=directory, config=configuration, model=ScriptedModel()
    ) as service:
        result = service.run("explain_project")
        assert result.status == "failed" and result.attempts == 0
    with pytest.raises(AIConfigurationError, match="outside"):
        WorkflowService(root, state_dir=root / "state")
    with pytest.raises(ValueError):
        GenerationConfig(model="gemini-latest")
    with sqlite3.connect(directory / "cache.sqlite3") as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1


def test_actual_langchain_adapter_boundary_without_network(demo, monkeypatch):
    import langchain_google_genai
    from langchain_core.messages import AIMessage

    from repotrail.ai_models import ProjectExplanation
    from repotrail.provider import GeminiGateway

    observed = {}

    class Adapter:
        def __init__(self, **kwargs):
            observed.update(kwargs)

        def with_structured_output(self, schema, **kwargs):
            assert schema is ProjectExplanation and kwargs == {
                "method": "json_schema",
                "include_raw": True,
            }
            return self

        def invoke(self, messages, config):
            assert config == {"callbacks": []}
            return {
                "parsed": ProjectExplanation(
                    summary=[],
                    structure=[],
                    entry_points=[],
                    selected_flows=[],
                    unknowns=["Unknown"],
                ),
                "raw": AIMessage(
                    content="",
                    usage_metadata={"input_tokens": 2, "output_tokens": 3, "total_tokens": 5},
                ),
            }

    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-test-placeholder")
    monkeypatch.setattr(langchain_google_genai, "ChatGoogleGenerativeAI", Adapter)
    configuration = GenerationConfig()
    gateway = GeminiGateway(configuration)
    assert observed["retries"] == 1 and observed["request_timeout"] == 30
    assert observed["max_tokens"] == 1500 and observed["vertexai"] is False
    assert observed["thinking_level"] == "low"
    reply = gateway.invoke("explain_project", [], configuration)
    assert reply.payload is not None and reply.usage["tokens"]["total_tokens"] == 5
    # The installed Google SDK defines attempts as TOTAL attempts including the original.
    from google.genai import types

    assert (
        "including the original"
        in types.HttpRetryOptions.model_fields["attempts"].description.lower()
    )
