from __future__ import annotations

import asyncio
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest

from astrbot_plugin_chat_tool_balance.services.llm_gateway import (
    AstrBotTransport,
    AstrBotTransportError,
    AstrBotTransportRequest,
    ClientFactoryResult,
    FallbackReasonCode,
    ResponsesClientFactory,
    ResponsesTransport,
    ResponsesTransportError,
    ResponsesTransportRequest,
)


class _FakeHTTPError(RuntimeError):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


class _FakeResponsesClient:
    def __init__(self, events: tuple[Any, ...] = (), error: Exception | None = None) -> None:
        self.events = events
        self.error = error
        self.calls: list[dict[str, Any]] = []
        self.responses = SimpleNamespace(create=self._create)

    def _create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return iter(self.events)


class _StaticClientFactory:
    def __init__(self, result: ClientFactoryResult) -> None:
        self.result = result
        self.provider_ids: list[str] = []

    def create_client(self, provider_id: str) -> ClientFactoryResult:
        self.provider_ids.append(provider_id)
        return self.result


@dataclass
class _FakeEvent:
    type: str
    delta: str = ""
    text: str = ""
    response: Any = None
    response_id: str = ""


class _AsyncRuntimeContext:
    def __init__(self, text: str = "fallback-ok", should_fail: bool = False, delay: float = 0.0) -> None:
        self.text = text
        self.should_fail = should_fail
        self.delay = delay
        self.calls: list[dict[str, Any]] = []

    async def llm_generate(
        self,
        chat_provider_id: str,
        prompt: str,
        contexts: tuple[str, ...] | None = None,
        event: Any = None,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "chat_provider_id": chat_provider_id,
                "prompt": prompt,
                "contexts": tuple(contexts or ()),
                "event": event,
            }
        )
        await asyncio.sleep(self.delay)
        if self.should_fail:
            raise RuntimeError("fallback_failed")
        return {"text": self.text}


def test_responses_client_factory_sdk_missing_returns_reason_code_success():
    def _missing_openai_builder(_provider_id: str) -> Any:
        raise ModuleNotFoundError("openai")

    factory = ResponsesClientFactory(client_builder=_missing_openai_builder)
    result = factory.create_client("provider-openai")
    assert result.ok is False
    assert result.reason_code == FallbackReasonCode.CAPABILITY_UNSUPPORTED
    assert "openai" in result.error


def test_responses_transport_stream_delta_done_completed_aggregate_success():
    events = (
        _FakeEvent(type="response.created", response=SimpleNamespace(id="resp_001", model="gpt-4.1")),
        _FakeEvent(type="response.output_text.delta", delta="hello"),
        _FakeEvent(type="response.output_text.delta", delta=" world"),
        _FakeEvent(type="response.output_text.done", text="hello world"),
        _FakeEvent(
            type="response.completed",
            response=SimpleNamespace(
                id="resp_001",
                model="gpt-4.1",
                usage={"input_tokens": 5, "output_tokens": 2},
            ),
        ),
    )
    client = _FakeResponsesClient(events=events)
    transport = ResponsesTransport(
        client_factory=_StaticClientFactory(
            result=ClientFactoryResult(provider_id="provider-openai", client=client)
        )
    )
    request = ResponsesTransportRequest(
        provider_id="provider-openai",
        model_name="gpt-4.1",
        instructions="sys",
        input="say hi",
        previous_response_id="resp_prev_001",
        metadata={"trace_id": "trace-001"},
    )

    result = transport.generate(request)

    assert result.text == "hello world"
    assert result.transport_used == "responses"
    assert result.response_id == "resp_001"
    assert result.model_name == "gpt-4.1"
    assert result.usage == {"input_tokens": 5, "output_tokens": 2}
    assert client.calls == [
        {
            "model": "gpt-4.1",
            "instructions": "sys",
            "input": "say hi",
            "stream": True,
            "previous_response_id": "resp_prev_001",
            "metadata": {"trace_id": "trace-001"},
        }
    ]


@pytest.mark.parametrize(
    ("error", "expected_reason_code"),
    [
        (_FakeHTTPError(404, "not found"), FallbackReasonCode.CAPABILITY_UNSUPPORTED),
        (_FakeHTTPError(501, "not implemented"), FallbackReasonCode.CAPABILITY_UNSUPPORTED),
        (_FakeHTTPError(429, "rate limit"), FallbackReasonCode.RESPONSES_RATE_LIMIT),
        (_FakeHTTPError(502, "bad gateway"), FallbackReasonCode.RESPONSES_SERVER_ERROR),
        (TimeoutError("request timeout"), FallbackReasonCode.RESPONSES_TIMEOUT),
    ],
)
def test_responses_transport_error_reason_mapping_success(error, expected_reason_code):
    client = _FakeResponsesClient(error=error)
    transport = ResponsesTransport(
        client_factory=_StaticClientFactory(
            result=ClientFactoryResult(provider_id="provider-openai", client=client)
        )
    )
    request = ResponsesTransportRequest(
        provider_id="provider-openai",
        model_name="gpt-4.1-mini",
        instructions="sys",
        input="ping",
    )

    with pytest.raises(ResponsesTransportError) as exc_info:
        transport.generate(request)
    assert exc_info.value.reason_code == expected_reason_code


def test_astrbot_transport_sync_bridge_success():
    runtime_context = _AsyncRuntimeContext(text="fallback-result", should_fail=False, delay=0.0)
    transport = AstrBotTransport(runtime_context=runtime_context, timeout_seconds=0.2)
    request = AstrBotTransportRequest(
        provider_id="provider-openai",
        model_name="gpt-4.1-mini",
        instructions="You are a helpful assistant.",
        input="Hello",
        event_context={"unified_msg_origin": "group:1001"},
    )

    result = transport.generate(request)

    assert result.text == "fallback-result"
    assert result.transport_used == "fallback_chat"
    assert result.provider_id == "provider-openai"
    assert result.model_name == "gpt-4.1-mini"
    assert len(runtime_context.calls) == 1
    first_call = runtime_context.calls[0]
    assert first_call["chat_provider_id"] == "provider-openai"
    assert "helpful assistant" in first_call["prompt"]
    assert "Hello" in first_call["prompt"]


def test_astrbot_transport_failure_raises_fallback_failed_success():
    runtime_context = _AsyncRuntimeContext(text="will-timeout", should_fail=False, delay=0.2)
    transport = AstrBotTransport(runtime_context=runtime_context, timeout_seconds=0.05)
    request = AstrBotTransportRequest(
        provider_id="provider-openai",
        model_name="gpt-4.1-mini",
        instructions="sys",
        input="ping",
    )

    with pytest.raises(AstrBotTransportError) as exc_info:
        transport.generate(request)
    assert exc_info.value.reason_code == FallbackReasonCode.FALLBACK_FAILED
