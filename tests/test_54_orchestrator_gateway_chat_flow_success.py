from __future__ import annotations

from types import SimpleNamespace

from pipeline.contracts import NormalizedEvent
from pipeline.orchestrator import ChatToolBalanceOrchestrator
from plugin_config import load_plugin_settings
from services.llm_gateway import FallbackReasonCode, GatewayResult
from services.runtime_wiring import build_runtime_wiring
from storage.bootstrap import initialize_storage


class _FakeRuntimeContext:
    def __init__(self, config: dict) -> None:
        self.config = config
        self.llm_calls: list[dict[str, object]] = []

    def get_plugin(self, _name: str):
        return None

    async def llm_generate(self, **kwargs):
        self.llm_calls.append(dict(kwargs))
        return "gateway-fallback-reply"


class _CaptureGateway:
    def __init__(self, result: GatewayResult) -> None:
        self.result = result
        self.calls = []

    def chat_with_state_sync(self, request):
        self.calls.append(request)
        return self.result


class _CaptureResponsesTransport:
    def __init__(self) -> None:
        self.calls = []

    def generate(self, request):
        self.calls.append(request)
        return GatewayResult(
            text="gateway-responses-reply",
            transport_used="responses",
            provider_id=request.provider_id,
            model_name=request.model_name,
            response_id="resp_runtime_gateway_001",
        )


def test_runtime_wiring_inject_gateway_and_chat_flow_success(tmp_path):
    base_dir = str(tmp_path / "plugin_data")
    config = {
        "models": {
            "chat_default": "provider-openai",
            "chat_model": "gpt-4.1-mini",
            "tool_intent_classifier": "tool-model",
            "topic_classifier": "topic-model",
        },
        "features": {"use_responses_api": False},
        "summary": {"enabled": False},
        "storage": {"base_dir": base_dir},
    }
    context = _FakeRuntimeContext(config=config)
    plugin = SimpleNamespace(config=config)

    wiring = build_runtime_wiring(context, plugin)

    assert wiring.llm_gateway is not None
    assert wiring.orchestrator.llm_gateway is wiring.llm_gateway

    reply = wiring.orchestrator.handle_event(
        NormalizedEvent(
            message_id="msg-runtime-gateway-1",
            session_id="session-runtime-gateway",
            scope_id="scope-runtime-gateway",
            user_id="u-runtime-gateway",
            text="你好",
        ),
        event_context={"unified_msg_origin": "origin-runtime-gateway"},
    )

    assert reply.route == "chat"
    assert reply.reply_text == "gateway-fallback-reply"
    assert reply.metadata["transport_used"] == "fallback_chat"
    assert reply.metadata["fallback_reason_code"] == FallbackReasonCode.CAPABILITY_UNSUPPORTED.value
    assert context.llm_calls


def test_orchestrator_chat_fallback_routes_to_gateway_with_metadata_success(tmp_path):
    base_dir = str(tmp_path / "plugin_data")
    bootstrap = initialize_storage(base_dir=base_dir, bucket_count=10)
    settings = load_plugin_settings(
        {
            "models": {
                "chat_default": "chat-model",
                "tool_intent_classifier": "tool-model",
                "topic_classifier": "topic-model",
            },
            "summary": {"enabled": False},
            "storage": {"base_dir": base_dir},
        }
    )

    capture_gateway = _CaptureGateway(
        result=GatewayResult(
            text="gateway-chat-result",
            transport_used="fallback_chat",
            provider_id="provider-openai",
            model_name="provider-openai",
            fallback_reason_code=FallbackReasonCode.RESPONSES_TIMEOUT,
        )
    )

    orchestrator = ChatToolBalanceOrchestrator(
        settings=settings,
        path_manager=bootstrap.path_manager,
        llm_gateway=capture_gateway,
        tool_executor=lambda _event, _prompt: "",
    )

    reply = orchestrator.handle_event(
        NormalizedEvent(
            message_id="msg-tool-fallback-gateway-1",
            session_id="session-tool-fallback-gateway",
            scope_id="scope-tool-fallback-gateway",
            user_id="u-tool-fallback-gateway",
            text="请帮我查询今天的天气",
        ),
        event_context={"unified_msg_origin": "origin-tool-fallback"},
    )

    assert reply.route == "chat"
    assert reply.reply_text == "gateway-chat-result"
    assert reply.fallback_used is True
    assert reply.metadata["tool_fallback_reason"] == "tool_empty_result"
    assert reply.metadata["transport_used"] == "fallback_chat"
    assert reply.metadata["fallback_reason_code"] == FallbackReasonCode.RESPONSES_TIMEOUT.value

    assert len(capture_gateway.calls) == 1
    request = capture_gateway.calls[0]
    assert request.scope_id == "scope-tool-fallback-gateway"
    assert request.topic_id == reply.topic_id
    assert request.metadata["message_id"] == "msg-tool-fallback-gateway-1"
    assert request.event_context["unified_msg_origin"] == "origin-tool-fallback"


def test_runtime_wiring_feature_on_prefers_responses_transport_success(tmp_path):
    base_dir = str(tmp_path / "plugin_data")
    config = {
        "models": {
            "chat_default": "provider-openai",
            "chat_model": "gpt-4.1-mini",
            "tool_intent_classifier": "tool-model",
            "topic_classifier": "topic-model",
        },
        "features": {"use_responses_api": True},
        "summary": {"enabled": False},
        "storage": {"base_dir": base_dir},
    }
    context = _FakeRuntimeContext(config=config)
    plugin = SimpleNamespace(config=config)
    wiring = build_runtime_wiring(context, plugin)

    capture_responses_transport = _CaptureResponsesTransport()
    wiring.llm_gateway.responses_transport = capture_responses_transport

    reply = wiring.orchestrator.handle_event(
        NormalizedEvent(
            message_id="msg-runtime-responses-1",
            session_id="session-runtime-responses",
            scope_id="scope-runtime-responses",
            user_id="u-runtime-responses",
            text="请介绍一下自己",
        ),
        event_context={"unified_msg_origin": "origin-runtime-responses"},
    )

    assert reply.route == "chat"
    assert reply.reply_text == "gateway-responses-reply"
    assert reply.metadata["transport_used"] == "responses"
    assert reply.metadata["fallback_reason_code"] == ""
    assert len(capture_responses_transport.calls) >= 1
    assert any(
        request.metadata.get("message_id") == "msg-runtime-responses-1"
        for request in capture_responses_transport.calls
    )
    assert any(request.model_name == "gpt-4.1-mini" for request in capture_responses_transport.calls)
    assert context.llm_calls == []
