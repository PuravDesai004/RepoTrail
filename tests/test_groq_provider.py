"""Provider identity, strict wire schema, real adapter transport, and failure boundaries."""

import json

import pytest

from repotrail.ai_models import AIConfigurationError, GenerationConfig, ReviewFinding
from repotrail.cli import build_parser
from repotrail.product_models import stable_digest
from repotrail.provider import GroqGateway, create_model, strict_output_schema
from repotrail.workflow import WorkflowService


def test_explicit_provider_defaults_and_mismatches(monkeypatch):
    assert GenerationConfig().model == "gemini-3.8-flash"
    config = GenerationConfig(provider="groq")
    assert config.model == "openai/gpt-oss-20b"
    assert config.prompt_version == "3"
    with pytest.raises(ValueError):
        GenerationConfig(provider="groq", model="gemini-3.8-flash")
    with pytest.raises(ValueError):
        GenerationConfig(model="openai/gpt-oss-20b")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "unrelated-provider-key")
    with pytest.raises(AIConfigurationError, match="GROQ_API_KEY"):
        create_model(config)
    assert stable_digest(config.model_dump()) != stable_digest(GenerationConfig().model_dump())
    args = build_parser().parse_args(["explain", "/fixture", "--provider", "groq"])
    assert args.provider == "groq" and args.model is None


@pytest.mark.parametrize("task", ["explain_project", "explain_changes", "review_diff"])
def test_strict_wire_schema_constrains_singletons_and_defaults(task):
    schema = strict_output_schema(task)

    def check(value):
        if isinstance(value, dict):
            assert "const" not in value and "default" not in value
            if "properties" in value:
                assert value["required"] == list(value["properties"])
                assert value["additionalProperties"] is False
            for item in value.values():
                check(item)
        elif isinstance(value, list):
            for item in value:
                check(item)

    check(schema)
    if task == "review_diff":
        assert schema["$defs"]["ReviewFinding"]["properties"]["basis"]["enum"] == ["inferred"]
    assert ReviewFinding.model_json_schema()["properties"]["basis"]["enum"] == ["inferred"]


@pytest.mark.parametrize("task", ["explain_project", "review_diff"])
def test_real_groq_adapter_sends_strict_schema_and_parses(monkeypatch, task):
    monkeypatch.setenv("GROQ_API_KEY", "offline-groq-placeholder")
    config = GenerationConfig(provider="groq")
    gateway = create_model(config)
    assert isinstance(gateway, GroqGateway)
    calls = []
    payload = (
        {"summary": [], "structure": [], "entry_points": [], "selected_flows": [], "unknowns": []}
        if task == "explain_project"
        else {"observations": [], "findings": [], "unknowns": []}
    )

    def complete(**kwargs):
        calls.append(kwargs)
        return {
            "id": "offline-completion",
            "created": 0,
            "object": "chat.completion",
            "model": config.model,
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": json.dumps(payload)},
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }

    monkeypatch.setattr(gateway.client.client, "create", complete)
    reply = gateway.invoke(task, [], config)
    assert reply.error_code is None and reply.payload == payload
    assert len(calls) == 1
    request = calls[0]
    assert request["response_format"]["type"] == "json_schema"
    assert request["response_format"]["json_schema"]["strict"] is True
    assert request["model"] == config.model and request["max_tokens"] == 1500
    assert gateway.client.client._client.max_retries == 0
    assert gateway.client.request_timeout == 30
    assert reply.usage["tokens"]["total_tokens"] == 15
    assert "offline-groq-placeholder" not in repr(reply)


def test_provider_backend_isolation_without_network(repo, tmp_path):
    root = repo
    with WorkflowService(root, state_dir=tmp_path / "gemini") as first:
        with WorkflowService(
            root, state_dir=tmp_path / "groq", config=GenerationConfig(provider="groq")
        ) as second:
            assert first.backend == "langchain-gemini" and second.backend == "langchain-groq"
            assert first.configuration_fingerprint(
                "review_diff"
            ) != second.configuration_fingerprint("review_diff")


def test_invalid_review_basis_is_rejected_without_coercion():
    class Adapter:
        def with_structured_output(self, *args, **kwargs):
            return self

        def invoke(self, *args, **kwargs):
            return {
                "parsed": {
                    "observations": [],
                    "findings": [
                        {
                            "severity": "medium",
                            "issue": "Synthetic risk",
                            "citations": ["synthetic"],
                            "reasoning": "Synthetic inference",
                            "suggested_validation": "Check fixture",
                            "basis": "observed",
                        }
                    ],
                    "unknowns": [],
                }
            }

    gateway = object.__new__(GroqGateway)
    gateway.client = Adapter()
    result = gateway.invoke("review_diff", [], GenerationConfig(provider="groq"))
    assert result.error_code == "invalid_schema" and result.payload is None
