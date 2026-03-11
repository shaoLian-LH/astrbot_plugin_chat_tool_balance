from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from astrbot_plugin_chat_tool_balance.services.llm_gateway import (
    CapabilityDecision,
    ChatSyncRequest,
    FallbackReasonCode,
    GatewayResult,
    GenerateSyncRequest,
    LLMGateway,
    ProviderResolution,
    ResponsesTransportError,
)


class _FakeProviderResolver:
    def __init__(self, provider_id: str = "provider-openai") -> None:
        self.provider_id = provider_id
        self.calls: list[dict[str, Any]] = []

    def resolve_provider(self, provider_role: str, event_context: Any = None) -> ProviderResolution:
        self.calls.append({"provider_role": provider_role, "event_context": event_context})
        return ProviderResolution(provider_id=self.provider_id, source="tests.fake")


class _FakeCapabilityRouter:
    def __init__(self, decision: CapabilityDecision) -> None:
        self.decision = decision
        self.calls: list[str] = []

    def decide(self, provider_id: str) -> CapabilityDecision:
        self.calls.append(provider_id)
        return self.decision


class _FakeResponsesTransport:
    def __init__(self, result: GatewayResult | None = None, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def generate(self, request: Any) -> GatewayResult:
        self.calls.append(
            {
                "provider_id": request.provider_id,
                "model_name": request.model_name,
                "instructions": request.instructions,
                "input": request.input,
                "previous_response_id": request.previous_response_id,
                "metadata": dict(request.metadata),
            }
        )
        if self.error is not None:
            raise self.error
        assert self.result is not None
        return self.result


class _FakeAstrBotTransport:
    def __init__(self, result: GatewayResult) -> None:
        self.result = result
        self.calls: list[dict[str, Any]] = []

    def generate(self, request: Any) -> GatewayResult:
        self.calls.append(
            {
                "provider_id": request.provider_id,
                "model_name": request.model_name,
                "instructions": request.instructions,
                "input": request.input,
                "metadata": dict(request.metadata),
                "event_context": request.event_context,
            }
        )
        return self.result


@dataclass
class _FakeStateRepository:
    current_state: dict[tuple[str, str], str]
    get_calls: list[tuple[str, str]]
    upsert_calls: list[dict[str, str]]

    def __init__(self, initial: Mapping[tuple[str, str], str] | None = None) -> None:
        self.current_state = dict(initial or {})
        self.get_calls = []
        self.upsert_calls = []

    def get_previous_response_id(self, scope_id: str, topic_id: str) -> str | None:
        self.get_calls.append((scope_id, topic_id))
        return self.current_state.get((scope_id, topic_id))

    def upsert_state(
        self,
        scope_id: str,
        topic_id: str,
        previous_response_id: str,
        provider_id: str = "",
        model_name: str = "",
        updated_at: str | None = None,
    ) -> None:
        self.upsert_calls.append(
            {
                "scope_id": scope_id,
                "topic_id": topic_id,
                "previous_response_id": previous_response_id,
                "provider_id": provider_id,
                "model_name": model_name,
            }
        )
        self.current_state[(scope_id, topic_id)] = previous_response_id


def _build_gateway(
    *,
    decision: CapabilityDecision,
    responses_result: GatewayResult | None = None,
    responses_error: Exception | None = None,
    fallback_result: GatewayResult | None = None,
    state_repository: _FakeStateRepository | None = None,
) -> tuple[LLMGateway, _FakeResponsesTransport, _FakeAstrBotTransport, _FakeProviderResolver]:
    resolver = _FakeProviderResolver(provider_id="provider-openai")
    router = _FakeCapabilityRouter(decision=decision)
    responses_transport = _FakeResponsesTransport(result=responses_result, error=responses_error)
    astrbot_transport = _FakeAstrBotTransport(
        result=fallback_result
        or GatewayResult(
            text="fallback-ok",
            transport_used="fallback_chat",
            provider_id="provider-openai",
            model_name="provider-openai",
        )
    )
    gateway = LLMGateway(
        provider_resolver=resolver,
        capability_router=router,
        responses_transport=responses_transport,
        astrbot_transport=astrbot_transport,
        state_repository=state_repository,
    )
    return gateway, responses_transport, astrbot_transport, resolver


def test_chat_with_state_sync_responses_success_upsert_on_empty_text_success():
    state_repository = _FakeStateRepository(initial={("scope-a", "topic-a"): "resp_001"})
    gateway, responses_transport, astrbot_transport, _ = _build_gateway(
        decision=CapabilityDecision(provider_id="provider-openai", use_responses=True),
        responses_result=GatewayResult(
            text="",
            transport_used="responses",
            provider_id="provider-openai",
            model_name="gpt-4.1",
            response_id="resp_002",
        ),
        state_repository=state_repository,
    )

    result = gateway.chat_with_state_sync(
        ChatSyncRequest(
            scope_id="scope-a",
            topic_id="topic-a",
            instructions="sys",
            input="hello",
            metadata={"trace_id": "trace-1"},
        )
    )

    assert result.transport_used == "responses"
    assert result.response_id == "resp_002"
    assert responses_transport.calls[0]["previous_response_id"] == "resp_001"
    assert state_repository.upsert_calls == [
        {
            "scope_id": "scope-a",
            "topic_id": "topic-a",
            "previous_response_id": "resp_002",
            "provider_id": "provider-openai",
            "model_name": "gpt-4.1",
        }
    ]
    assert astrbot_transport.calls == []


def test_chat_with_state_sync_responses_fallback_skip_upsert_success():
    state_repository = _FakeStateRepository(initial={("scope-a", "topic-a"): "resp_001"})
    gateway, responses_transport, astrbot_transport, _ = _build_gateway(
        decision=CapabilityDecision(provider_id="provider-openai", use_responses=True),
        responses_error=ResponsesTransportError(
            reason_code=FallbackReasonCode.RESPONSES_TIMEOUT,
            detail="read_timeout",
        ),
        fallback_result=GatewayResult(
            text="fallback-timeout",
            transport_used="fallback_chat",
            provider_id="provider-openai",
            model_name="provider-openai",
        ),
        state_repository=state_repository,
    )

    result = gateway.chat_with_state_sync(
        ChatSyncRequest(
            scope_id="scope-a",
            topic_id="topic-a",
            instructions="sys",
            input="hello",
        )
    )

    assert result.transport_used == "fallback_chat"
    assert result.text == "fallback-timeout"
    assert result.fallback_reason_code == FallbackReasonCode.RESPONSES_TIMEOUT
    assert state_repository.upsert_calls == []
    assert state_repository.current_state[("scope-a", "topic-a")] == "resp_001"
    assert responses_transport.calls[0]["previous_response_id"] == "resp_001"
    assert len(astrbot_transport.calls) == 1


def test_generate_once_sync_never_touches_state_repository_success():
    state_repository = _FakeStateRepository(initial={("scope-a", "topic-a"): "resp_001"})
    gateway, responses_transport, astrbot_transport, resolver = _build_gateway(
        decision=CapabilityDecision(provider_id="provider-openai", use_responses=True),
        responses_result=GatewayResult(
            text="ocr-result",
            transport_used="responses",
            provider_id="provider-openai",
            model_name="gpt-4.1-mini",
            response_id="resp_ocr_001",
        ),
        state_repository=state_repository,
    )

    result = gateway.generate_once_sync(
        GenerateSyncRequest(
            provider_role="ocr",
            instructions="extract text",
            input={"image": "base64"},
            metadata={"task": "ocr"},
        )
    )

    assert result.transport_used == "responses"
    assert result.text == "ocr-result"
    assert state_repository.get_calls == []
    assert state_repository.upsert_calls == []
    assert responses_transport.calls[0]["previous_response_id"] is None
    assert astrbot_transport.calls == []
    assert resolver.calls[0]["provider_role"] == "ocr"


def test_chat_with_state_sync_model_name_resolver_decouples_provider_and_model_success():
    state_repository = _FakeStateRepository(initial={("scope-a", "topic-a"): "resp_001"})
    gateway, responses_transport, astrbot_transport, _ = _build_gateway(
        decision=CapabilityDecision(provider_id="provider-openai", use_responses=True),
        responses_result=GatewayResult(
            text="responses-ok",
            transport_used="responses",
            provider_id="provider-openai",
            model_name="gpt-4.1",
            response_id="resp_002",
        ),
        state_repository=state_repository,
    )
    gateway.model_name_resolver = lambda role, provider_id: "gpt-4.1-mini" if role == "chat" else provider_id

    result = gateway.chat_with_state_sync(
        ChatSyncRequest(
            scope_id="scope-a",
            topic_id="topic-a",
            instructions="sys",
            input="hello",
        )
    )

    assert result.transport_used == "responses"
    assert responses_transport.calls[0]["provider_id"] == "provider-openai"
    assert responses_transport.calls[0]["model_name"] == "gpt-4.1-mini"
    assert astrbot_transport.calls == []


def test_chat_with_state_sync_feature_off_direct_fallback_success():
    state_repository = _FakeStateRepository(initial={("scope-a", "topic-a"): "resp_001"})
    gateway, responses_transport, astrbot_transport, _ = _build_gateway(
        decision=CapabilityDecision(
            provider_id="provider-openai",
            use_responses=False,
            fallback_reason_code=FallbackReasonCode.CAPABILITY_UNSUPPORTED,
        ),
        responses_result=GatewayResult(
            text="responses-should-not-run",
            transport_used="responses",
            provider_id="provider-openai",
            model_name="gpt-4.1",
            response_id="resp_skip",
        ),
        fallback_result=GatewayResult(
            text="fallback-disabled",
            transport_used="fallback_chat",
            provider_id="provider-openai",
            model_name="provider-openai",
        ),
        state_repository=state_repository,
    )

    result = gateway.chat_with_state_sync(
        ChatSyncRequest(
            scope_id="scope-a",
            topic_id="topic-a",
            instructions="sys",
            input="hello",
        )
    )

    assert result.transport_used == "fallback_chat"
    assert result.text == "fallback-disabled"
    assert result.fallback_reason_code == FallbackReasonCode.CAPABILITY_UNSUPPORTED
    assert responses_transport.calls == []
    assert len(astrbot_transport.calls) == 1
    assert state_repository.upsert_calls == []
