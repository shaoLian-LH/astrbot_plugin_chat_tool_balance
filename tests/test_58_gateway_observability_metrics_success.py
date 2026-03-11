from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from astrbot_plugin_chat_tool_balance.scheduler.summary_state_janitor import SummaryStateJanitor
from astrbot_plugin_chat_tool_balance.services.llm_gateway import (
    CapabilityDecision,
    ChatSyncRequest,
    FallbackReasonCode,
    GatewayMetricsRecorder,
    GatewayResult,
    GenerateSyncRequest,
    LLMGateway,
    ProviderResolution,
    RESPONSE_STATE_CLEANUP_TOTAL,
    RESPONSE_STATE_HIT_TOTAL,
    RESPONSES_ATTEMPT_TOTAL,
    RESPONSES_FALLBACK_TOTAL,
    RESPONSES_LATENCY_MS_BUCKET,
    RESPONSES_SUCCESS_TOTAL,
)


class _FakeProviderResolver:
    def resolve_provider(self, provider_role: str, event_context: Any = None) -> ProviderResolution:
        return ProviderResolution(provider_id=f"provider-{provider_role}", source="test")


class _FakeCapabilityRouter:
    def __init__(self, decision: CapabilityDecision) -> None:
        self.decision = decision

    def decide(self, provider_id: str) -> CapabilityDecision:
        return self.decision


class _FakeResponsesTransport:
    def __init__(self, result: GatewayResult) -> None:
        self.result = result
        self.calls: list[object] = []

    def generate(self, request: object) -> GatewayResult:
        self.calls.append(request)
        return self.result


class _FakeAstrBotTransport:
    def __init__(self, result: GatewayResult) -> None:
        self.result = result
        self.calls: list[object] = []

    def generate(self, request: object) -> GatewayResult:
        self.calls.append(request)
        return self.result


class _CaptureLogger:
    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    def info(self, msg: str, *args: Any) -> None:
        payload = args[0] if args else {}
        self.records.append(
            {
                "msg": msg,
                "payload": payload if isinstance(payload, dict) else {},
            }
        )


@dataclass
class _FakeStateRepository:
    previous_response_id: str | None = "resp_prev_001"
    upsert_calls: list[dict[str, str]] = field(default_factory=list)

    def get_previous_response_id(self, scope_id: str, topic_id: str) -> str | None:
        assert scope_id == "scope-observe"
        assert topic_id == "topic-observe"
        return self.previous_response_id

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


def test_gateway_records_responses_metrics_and_logs_for_chat_success():
    metrics = GatewayMetricsRecorder()
    logger = _CaptureLogger()
    gateway = LLMGateway(
        provider_resolver=_FakeProviderResolver(),
        capability_router=_FakeCapabilityRouter(
            CapabilityDecision(provider_id="provider-chat", use_responses=True)
        ),
        responses_transport=_FakeResponsesTransport(
            GatewayResult(
                text="responses-ok",
                transport_used="responses",
                provider_id="provider-chat",
                model_name="gpt-4.1",
                response_id="resp_new_002",
            )
        ),
        astrbot_transport=_FakeAstrBotTransport(
            GatewayResult(
                text="fallback-unused",
                transport_used="fallback_chat",
                provider_id="provider-chat",
                model_name="provider-chat",
            )
        ),
        state_repository=_FakeStateRepository(),
        metrics_recorder=metrics,
        logger_obj=logger,
    )

    result = gateway.chat_with_state_sync(
        ChatSyncRequest(
            scope_id="scope-observe",
            topic_id="topic-observe",
            instructions="sys",
            input="hello",
            metadata={"message_id": "msg-observe-1"},
        )
    )

    assert result.transport_used == "responses"
    assert metrics.counter_value(RESPONSES_ATTEMPT_TOTAL, labels={"role": "chat"}) == 1
    assert metrics.counter_value(RESPONSES_SUCCESS_TOTAL, labels={"role": "chat"}) == 1
    assert metrics.counter_value(RESPONSE_STATE_HIT_TOTAL) == 1
    assert metrics.histogram_values(RESPONSES_LATENCY_MS_BUCKET, labels={"role": "chat"})
    gateway_records = [
        item["payload"]
        for item in logger.records
        if item["payload"].get("transport_used") == "responses"
    ]
    assert gateway_records
    payload = gateway_records[-1]
    assert payload["role"] == "chat"
    assert payload["provider_id"] == "provider-chat"
    assert payload["fallback_reason_code"] == ""
    assert payload["scope_id"] == "scope-observe"
    assert payload["topic_id"] == "topic-observe"
    assert payload["request_id"] == "msg-observe-1"
    assert "latency_ms" in payload


def test_gateway_records_fallback_metrics_and_logs_for_non_chat_success():
    metrics = GatewayMetricsRecorder()
    logger = _CaptureLogger()
    fallback_result = GatewayResult(
        text="fallback-ok",
        transport_used="fallback_chat",
        provider_id="provider-ocr",
        model_name="provider-ocr",
    )
    gateway = LLMGateway(
        provider_resolver=_FakeProviderResolver(),
        capability_router=_FakeCapabilityRouter(
            CapabilityDecision(
                provider_id="provider-ocr",
                use_responses=False,
                fallback_reason_code=FallbackReasonCode.CAPABILITY_UNSUPPORTED,
            )
        ),
        responses_transport=_FakeResponsesTransport(
            GatewayResult(
                text="responses-should-not-run",
                transport_used="responses",
                provider_id="provider-ocr",
                model_name="provider-ocr",
            )
        ),
        astrbot_transport=_FakeAstrBotTransport(fallback_result),
        metrics_recorder=metrics,
        logger_obj=logger,
    )

    result = gateway.generate_once_sync(
        GenerateSyncRequest(
            provider_role="ocr",
            instructions="ocr",
            input={"image": "x"},
            metadata={"message_id": "msg-ocr-1"},
        )
    )

    assert result.transport_used == "fallback_chat"
    assert metrics.counter_value(RESPONSES_ATTEMPT_TOTAL, labels={"role": "ocr"}) == 0
    assert (
        metrics.counter_value(
            RESPONSES_FALLBACK_TOTAL,
            labels={"role": "ocr", "reason": FallbackReasonCode.CAPABILITY_UNSUPPORTED.value},
        )
        == 1
    )
    fallback_logs = [
        item["payload"]
        for item in logger.records
        if item["payload"].get("transport_used") == "fallback_chat"
    ]
    assert fallback_logs
    payload = fallback_logs[-1]
    assert payload["role"] == "ocr"
    assert payload["fallback_reason_code"] == FallbackReasonCode.CAPABILITY_UNSUPPORTED.value
    assert payload["request_id"] == "msg-ocr-1"


def test_summary_state_janitor_records_cleanup_metric_and_log_success():
    metrics = GatewayMetricsRecorder()
    logger = _CaptureLogger()

    class _FakeRepo:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []

        def delete_by_scope_topic(self, scope_id: str, topic_id: str) -> int:
            self.calls.append((scope_id, topic_id))
            return 2

    repo = _FakeRepo()
    janitor = SummaryStateJanitor(
        state_repository=repo,  # type: ignore[arg-type]
        metrics_recorder=metrics,
        logger_obj=logger,
    )

    deleted = janitor.delete_by_scope_topic("scope-clean", "topic-clean")

    assert deleted == 2
    assert repo.calls == [("scope-clean", "topic-clean")]
    assert metrics.counter_value(RESPONSE_STATE_CLEANUP_TOTAL) == 2
    cleanup_logs = [
        item["payload"]
        for item in logger.records
        if item["payload"].get("response_state_op") == "delete"
    ]
    assert cleanup_logs
    payload = cleanup_logs[-1]
    assert payload["role"] == "summary"
    assert payload["scope_id"] == "scope-clean"
    assert payload["topic_id"] == "topic-clean"
