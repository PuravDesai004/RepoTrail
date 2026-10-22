"""Classify nested provider errors without leaking raw messages or retrying in the SDK."""

import pytest

from repotrail.ai_models import GenerationConfig
from repotrail.provider import GeminiGateway


@pytest.mark.parametrize(
    "code,expected",
    [
        (429, "rate_limit"),
        (403, "provider_configuration"),
        (400, "provider_configuration"),
        (404, "provider_configuration"),
        (503, "provider_unavailable"),
    ],
)
def test_wrapped_provider_status_is_classified_and_sanitized(code, expected):
    class Inner(Exception):
        pass

    class Adapter:
        def with_structured_output(self, *args, **kwargs):
            return self

        def invoke(self, *args, **kwargs):
            error = Inner("SENSITIVE_FAKE_KEY_MUST_NEVER_APPEAR")
            error.code = code
            raise RuntimeError("SENSITIVE_FAKE_KEY_MUST_NEVER_APPEAR") from error

    gateway = object.__new__(GeminiGateway)
    gateway.client = Adapter()
    result = gateway.invoke("explain_project", [], GenerationConfig())
    assert result.error_code == expected
    assert "SENSITIVE_FAKE_KEY" not in repr(result)
    assert result.retryable == (code in (429, 503))


def test_adapter_timeout_is_typed():
    class Adapter:
        def with_structured_output(self, *args, **kwargs):
            return self

        def invoke(self, *args, **kwargs):
            raise TimeoutError("private request text")

    gateway = object.__new__(GeminiGateway)
    gateway.client = Adapter()
    result = gateway.invoke("review_diff", [], GenerationConfig())
    assert result.error_code == "timeout" and "private request" not in repr(result)
