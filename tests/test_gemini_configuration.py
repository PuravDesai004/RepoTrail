"""Check the actual installed adapter's request configuration without network calls."""

import pytest
from langchain_core.messages import HumanMessage

from repotrail.ai_models import GenerationConfig
from repotrail.product_models import stable_digest
from repotrail.provider import GeminiGateway


@pytest.mark.parametrize("model", ["gemini-3.8-flash", "gemini-3.6-flash", "gemini-3.5-flash-lite"])
def test_stable_models_use_supported_reasoning_level(model, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "offline-configuration-placeholder")
    config = GenerationConfig(model=model)
    gateway = GeminiGateway(config)
    request = gateway.client._prepare_request(
        [HumanMessage(content="Offline configuration check.")]
    )
    assert gateway.client.reasoning_effort == "low"
    assert str(request["config"].thinking_config.thinking_level).upper().endswith("LOW")
    assert request["config"].max_output_tokens == 1500


def test_reasoning_level_is_validated_and_changes_fingerprint():
    with pytest.raises(ValueError):
        GenerationConfig(thinking_level="minimal")
    assert stable_digest(GenerationConfig().model_dump()) != stable_digest(
        GenerationConfig(thinking_level="medium").model_dump()
    )
