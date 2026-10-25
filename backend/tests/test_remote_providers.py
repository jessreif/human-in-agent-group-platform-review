"""Remote provider adapters (MODEL_PROVIDER=openai/minimax) — request shape, endpoint
guards, error mapping, live-mode refusal, and settings dispatch. No network: transports
are injected doubles."""

from __future__ import annotations

import json
import urllib.error
from typing import Any

import pytest

from app.providers.base import (
    Message,
    ProviderError,
    ProviderErrorKind,
    ProviderNotAllowedError,
    ProviderRequest,
    ensure_provider_allowed,
)
from app.providers.remote import (
    RemoteMiniMaxProvider,
    RemoteOpenAIProvider,
    RemoteProviderConfigError,
)
from app.providers.select import provider_from_settings
from app.settings import load_settings

OPENAI_BASE = "https://api.openai.com/v1"
MINIMAX_BASE = "https://api.minimax.io/anthropic"
MODEL = "test-model"
KEY = "test-key"

Call = tuple[str, dict[str, Any], dict[str, str], float]


def _request(**params: Any) -> ProviderRequest:
    return ProviderRequest(
        purpose="assistant",
        prompt_id="test",
        messages=(
            Message("system", "sys text"),
            Message("user", "prompt text"),
        ),
        params=params,
        request_id="r1",
    )


def _openai(
    calls: list[Call] | None = None,
    status: int = 200,
    body: bytes | None = None,
    transport: Any = None,
):
    def default_transport(url: str, raw: bytes, headers: dict[str, str], timeout_s: float):
        if calls is not None:
            calls.append((url, json.loads(raw), headers, timeout_s))
        return status, body or json.dumps(
            {
                "model": MODEL,
                "choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}],
            }
        ).encode()

    return RemoteOpenAIProvider(
        OPENAI_BASE,
        model=MODEL,
        api_key=KEY,
        max_concurrency=2,
        transport=transport or default_transport,
    )


def _minimax(
    calls: list[Call] | None = None,
    status: int = 200,
    body: bytes | None = None,
    transport: Any = None,
):
    def default_transport(url: str, raw: bytes, headers: dict[str, str], timeout_s: float):
        if calls is not None:
            calls.append((url, json.loads(raw), headers, timeout_s))
        return status, body or json.dumps(
            {
                "model": MODEL,
                "content": [{"type": "text", "text": "hi"}],
                "stop_reason": "end_turn",
            }
        ).encode()

    return RemoteMiniMaxProvider(
        MINIMAX_BASE,
        model=MODEL,
        api_key=KEY,
        max_concurrency=2,
        transport=transport or default_transport,
    )


# ----- endpoint / config guards -----------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://api.openai.com/v1",
        "http://127.0.0.1:11434",
        "not-a-url",
        "https://user:pw@api.openai.com/v1",
        "https://api.openai.com/v1?token=abc",
    ],
)
def test_remote_rejects_non_https_or_credentialed_urls(url: str) -> None:
    with pytest.raises(RemoteProviderConfigError):
        RemoteOpenAIProvider(url, model=MODEL, api_key=KEY, max_concurrency=1)


def test_remote_requires_key_and_model() -> None:
    with pytest.raises(RemoteProviderConfigError):
        RemoteOpenAIProvider(OPENAI_BASE, model="", api_key=KEY, max_concurrency=1)
    with pytest.raises(RemoteProviderConfigError):
        RemoteOpenAIProvider(OPENAI_BASE, model=MODEL, api_key="  ", max_concurrency=1)


# ----- request/response shape -------------------------------------------------------------


def test_openai_request_shape_and_auth_header() -> None:
    calls: list[Call] = []
    resp = _openai(calls=calls).generate(_request(temperature=0.2), timeout_s=5.0)
    assert resp.text == "hi"
    assert resp.provider_id == "openai_chat"
    assert resp.model_id == MODEL
    url, body, headers, timeout = calls[0]
    assert url == f"{OPENAI_BASE}/chat/completions"
    assert headers["Authorization"] == f"Bearer {KEY}"
    assert "x-api-key" not in headers
    assert body["model"] == MODEL
    assert body["temperature"] == 0.2
    assert body["messages"] == [
        {"role": "system", "content": "sys text"},
        {"role": "user", "content": "prompt text"},
    ]
    assert 4.9 <= timeout <= 5.0


def test_minimax_request_shape_moves_system_and_sets_max_tokens() -> None:
    calls: list[Call] = []
    resp = _minimax(calls=calls).generate(_request(temperature=0.2), timeout_s=5.0)
    assert resp.text == "hi"
    assert resp.provider_id == "minimax_anthropic"
    assert resp.finish_reason == "end_turn"
    url, body, headers, _ = calls[0]
    assert url == f"{MINIMAX_BASE}/v1/messages"
    assert headers["x-api-key"] == KEY
    assert headers["anthropic-version"] == "2023-06-01"
    assert body["system"] == "sys text"
    assert body["messages"] == [{"role": "user", "content": "prompt text"}]
    assert body["max_tokens"] == 400
    assert body["temperature"] == 0.2


def test_model_pin_mismatch_rejected() -> None:
    with pytest.raises(ProviderError) as exc:
        _openai().generate(_request(model_pin="other-model"), timeout_s=5.0)
    assert exc.value.kind == ProviderErrorKind.INVALID_RESPONSE


def test_served_model_mismatch_rejected() -> None:
    body = json.dumps({"model": "different", "choices": [{"message": {"content": "x"}}]}).encode()
    with pytest.raises(ProviderError) as exc:
        _openai(body=body).generate(_request(), timeout_s=5.0)
    assert exc.value.kind == ProviderErrorKind.INVALID_RESPONSE


@pytest.mark.parametrize("provider_fn", [_openai, _minimax])
def test_http_error_maps_to_unavailable(provider_fn) -> None:
    with pytest.raises(ProviderError) as exc:
        provider_fn(status=500, body=b"{}").generate(_request(), timeout_s=5.0)
    assert exc.value.kind == ProviderErrorKind.UNAVAILABLE


@pytest.mark.parametrize(
    ("provider_fn", "adapter"),
    [
        (_openai, RemoteOpenAIProvider),
        (_minimax, RemoteMiniMaxProvider),
    ],
)
def test_transport_timeout_maps_to_timeout(provider_fn, adapter) -> None:
    def transport(url: str, raw: bytes, headers: dict[str, str], timeout_s: float):
        raise urllib.error.URLError(TimeoutError())

    provider = provider_fn(transport=transport)
    assert type(provider) is adapter
    with pytest.raises(ProviderError) as exc:
        provider.generate(_request(), timeout_s=5.0)
    assert exc.value.kind == ProviderErrorKind.TIMEOUT


@pytest.mark.parametrize("provider_fn", [_openai, _minimax])
def test_malformed_body_maps_to_invalid(provider_fn) -> None:
    with pytest.raises(ProviderError) as exc:
        provider_fn(body=b"not json").generate(_request(), timeout_s=5.0)
    assert exc.value.kind == ProviderErrorKind.INVALID_RESPONSE


# ----- mode gating ------------------------------------------------------------------------


def test_remote_providers_refused_for_session_traffic_in_every_mode() -> None:
    """BA-35: APP_MODE is not a participant-data boundary — preview/QA sessions can
    carry a human tester's text, so remote adapters are refused for session work in
    every mode. They remain constructible for offline fixture use only."""
    for provider in (_openai(), _minimax()):
        for mode in ("preview", "qa", "live"):
            with pytest.raises(ProviderNotAllowedError):
                ensure_provider_allowed(mode, provider)


def test_remote_declares_no_reasoning_effort_support() -> None:
    assert _openai().supports_reasoning_effort is False
    assert _minimax().supports_reasoning_effort is False


# ----- settings dispatch ------------------------------------------------------------------


def _settings(**overrides: Any):
    return load_settings(
        APP_MODE="preview",
        DATABASE_URL="postgresql://u:p@127.0.0.1:5432/test_db",
        **overrides,
    )


def test_dispatch_auto_picks_local_when_configured(monkeypatch: Any) -> None:
    digest = "ab" * 32
    monkeypatch.setattr(
        "app.providers.local._urllib_get_transport",
        lambda url, timeout_s: (
            200,
            json.dumps(
                {"models": [{"name": "m", "model": "m", "digest": f"sha256:{digest}"}]}
            ).encode(),
        ),
    )
    s = _settings(
        MODEL_PROVIDER="auto",
        LOCAL_MODEL_BASE_URL="http://127.0.0.1:11434",
        LOCAL_MODEL_ID="m",
        LOCAL_MODEL_DIGEST=digest,
    )
    provider = provider_from_settings(s)
    assert provider is not None and provider.provider_id == "local_openai_chat"


def test_dispatch_local_requires_digest_pin() -> None:
    """BA-40: a configured local endpoint without LOCAL_MODEL_DIGEST is rejected at
    settings validation — the worker cannot start serving unverified generations."""
    with pytest.raises(Exception, match="LOCAL_MODEL_DIGEST"):
        _settings(
            MODEL_PROVIDER="auto",
            LOCAL_MODEL_BASE_URL="http://127.0.0.1:11434",
            LOCAL_MODEL_ID="m",
        )
    with pytest.raises(Exception, match="LOCAL_MODEL_DIGEST"):
        _settings(
            MODEL_PROVIDER="ollama",
            LOCAL_MODEL_BASE_URL="http://127.0.0.1:11434",
            LOCAL_MODEL_ID="m",
        )


def test_dispatch_none_forces_deterministic() -> None:
    s = _settings(
        MODEL_PROVIDER="none",
        LOCAL_MODEL_BASE_URL="http://127.0.0.1:11434",
        LOCAL_MODEL_ID="m",
    )
    assert provider_from_settings(s) is None


def test_dispatch_openai_builds_remote() -> None:
    s = _settings(
        MODEL_PROVIDER="openai",
        OPENAI_API_KEY="k",
        OPENAI_MODEL_ID="m",
    )
    provider = provider_from_settings(s)
    assert provider is not None and provider.provider_id == "openai_chat"


def test_dispatch_minimax_builds_remote() -> None:
    s = _settings(
        MODEL_PROVIDER="minimax",
        MINIMAX_API_KEY="k",
        MINIMAX_MODEL_ID="m",
    )
    provider = provider_from_settings(s)
    assert provider is not None and provider.provider_id == "minimax_anthropic"


def test_dispatch_openai_requires_key_and_model() -> None:
    with pytest.raises(Exception) as exc:
        _settings(MODEL_PROVIDER="openai", OPENAI_API_KEY="", OPENAI_MODEL_ID="m")
    assert "OPENAI_API_KEY" in str(exc.value)
    with pytest.raises(Exception) as exc:
        _settings(MODEL_PROVIDER="openai", OPENAI_API_KEY="k", OPENAI_MODEL_ID="")
    assert "OPENAI_MODEL_ID" in str(exc.value)


def test_dispatch_ollama_requires_local_config() -> None:
    with pytest.raises(ValueError, match="LOCAL_MODEL_BASE_URL"):
        provider_from_settings(_settings(MODEL_PROVIDER="ollama"))


def test_settings_describe_is_value_free() -> None:
    s = _settings(MODEL_PROVIDER="openai", OPENAI_API_KEY="supersecret", OPENAI_MODEL_ID="m")
    described = s.describe()
    assert described["model_provider"] == "openai"
    assert described["remote_model_configured"] is True
    assert "supersecret" not in json.dumps(described)
