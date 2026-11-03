"""Exercise the installed adapter's real parser without a provider request."""

import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_google_genai import ChatGoogleGenerativeAI

from repotrail.ai_models import GenerationConfig
from repotrail.provider import GeminiGateway


def stub_generation(monkeypatch, payload, finish_reason="STOP"):
    calls = []

    def generate(self, messages, **kwargs):
        calls.append(kwargs)
        return ChatResult(
            generations=[
                ChatGeneration(
                    message=AIMessage(
                        content=json.dumps(payload),
                        response_metadata={"finish_reason": finish_reason},
                        usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
                    )
                )
            ]
        )

    monkeypatch.setenv("GEMINI_API_KEY", "offline-parser-placeholder")
    monkeypatch.setattr(ChatGoogleGenerativeAI, "_generate", generate)
    return calls


@pytest.mark.parametrize(
    "task,payload",
    [
        (
            "explain_project",
            {
                "summary": [],
                "structure": [],
                "entry_points": [],
                "selected_flows": [],
                "unknowns": [],
            },
        ),
        ("review_diff", {"observations": [], "findings": [], "unknowns": []}),
    ],
)
def test_real_structured_parser_accepts_valid_json(monkeypatch, task, payload):
    calls = stub_generation(monkeypatch, payload)
    config = GenerationConfig()
    result = GeminiGateway(config).invoke(task, [HumanMessage(content="Synthetic input")], config)
    assert result.payload == payload and result.error_code is None
    assert len(calls) == 1
    assert calls[0]["response_mime_type"] == "application/json"
    assert "response_json_schema" in calls[0]


@pytest.mark.parametrize("finish_reason", ["STOP", "MAX_TOKENS", "PRIVATE_RESPONSE_TEXT"])
def test_real_structured_parse_failure_has_safe_diagnostics(monkeypatch, finish_reason):
    calls = stub_generation(monkeypatch, {"observations": "PRIVATE_RESPONSE_TEXT"}, finish_reason)
    config = GenerationConfig()
    result = GeminiGateway(config).invoke(
        "review_diff", [HumanMessage(content="Synthetic")], config
    )
    assert result.payload is None and result.error_code == "invalid_schema"
    assert len(calls) == 1
    assert "OutputParserException" in result.usage["parsing_error_types"]
    assert "PRIVATE_RESPONSE_TEXT" not in repr(result)
    assert "offline-parser-placeholder" not in repr(result)
    assert result.usage["tokens"]["total_tokens"] == 15
    if finish_reason != "PRIVATE_RESPONSE_TEXT":
        assert result.usage["finish_reason"] == finish_reason
    else:
        assert "finish_reason" not in result.usage
