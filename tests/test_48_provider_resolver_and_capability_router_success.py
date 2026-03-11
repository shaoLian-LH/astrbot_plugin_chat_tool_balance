import asyncio
from types import SimpleNamespace

import pytest

from astrbot_plugin_chat_tool_balance.services.llm_gateway import (
    CapabilityRouter,
    FallbackReasonCode,
    ProviderResolutionError,
    ProviderResolver,
)
from astrbot_plugin_chat_tool_balance.services.llm_gateway.capability_router import map_probe_error_to_reason_code


class _RuntimeProviderContext:
    def __init__(self, provider_id: str):
        self.provider_id = provider_id
        self.calls: list[str | None] = []

    def get_current_chat_provider_id(self, umo: str | None = None) -> str:
        self.calls.append(umo)
        return self.provider_id


class _AsyncRuntimeProviderContext:
    def __init__(self, provider_id: str):
        self.provider_id = provider_id
        self.calls: list[str | None] = []

    async def get_current_chat_provider_id(self, umo: str | None = None) -> str:
        self.calls.append(umo)
        await asyncio.sleep(0)
        return self.provider_id


class _FakeClock:
    def __init__(self, now_epoch: float = 0.0):
        self.now_epoch = now_epoch

    def __call__(self) -> float:
        return self.now_epoch

    def forward(self, seconds: float) -> None:
        self.now_epoch += seconds


def test_provider_resolver_role_priority_and_runtime_fallback_success():
    resolver = ProviderResolver(
        models={
            "chat_default": "provider-chat-default",
            "ocr": "provider-ocr",
            "topic_classifier": "",
            "tool_intent_classifier": "provider-tool-intent",
            "summary": "",
        },
        runtime_context=_RuntimeProviderContext(provider_id="provider-runtime"),
    )

    assert resolver.resolve_provider_id("ocr") == "provider-ocr"
    assert resolver.resolve_provider_id("tool_intent_classifier") == "provider-tool-intent"
    assert resolver.resolve_provider_id("chat") == "provider-chat-default"
    assert resolver.resolve_provider_id("topic_classifier") == "provider-chat-default"
    assert resolver.resolve_provider_id("summary") == "provider-chat-default"

    runtime_context = _RuntimeProviderContext(provider_id="provider-runtime")
    runtime_resolver = ProviderResolver(
        models={
            "chat_default": "",
            "ocr": "",
            "topic_classifier": "",
            "tool_intent_classifier": "",
            "summary": "",
        },
        runtime_context=runtime_context,
    )
    event_context = SimpleNamespace(unified_msg_origin="group:1001")

    assert runtime_resolver.resolve_provider_id("summary", event_context=event_context) == "provider-runtime"
    assert runtime_context.calls == ["group:1001"]


def test_provider_resolver_no_provider_raises_diagnostic_error_success():
    resolver = ProviderResolver(
        models={
            "chat_default": "",
            "ocr": "",
            "topic_classifier": "",
            "tool_intent_classifier": "",
            "summary": "",
        },
        runtime_context=SimpleNamespace(),
    )

    with pytest.raises(ProviderResolutionError) as exc_info:
        resolver.resolve_provider_id("chat")
    assert exc_info.value.code == "provider_not_configured"


def test_provider_resolver_async_runtime_getter_under_running_loop_success():
    runtime_context = _AsyncRuntimeProviderContext(provider_id="provider-runtime-async")
    resolver = ProviderResolver(
        models={
            "chat_default": "",
            "ocr": "",
            "topic_classifier": "",
            "tool_intent_classifier": "",
            "summary": "",
        },
        runtime_context=runtime_context,
    )
    event_context = SimpleNamespace(unified_msg_origin="group:2002")

    async def _run_inside_event_loop() -> str:
        return resolver.resolve_provider_id("chat", event_context=event_context)

    resolved_provider = asyncio.run(_run_inside_event_loop())
    assert resolved_provider == "provider-runtime-async"
    assert runtime_context.calls == ["group:2002"]


def test_capability_router_feature_toggle_off_force_fallback_success():
    probe_calls = 0

    def _probe(_provider_id: str) -> bool:
        nonlocal probe_calls
        probe_calls += 1
        return True

    router = CapabilityRouter(
        use_responses_api=False,
        supports_responses_probe=_probe,
    )
    decision = router.decide("provider-a")

    assert decision.use_responses is False
    assert decision.fallback_reason_code == FallbackReasonCode.CAPABILITY_UNSUPPORTED
    assert decision.cache_hit is False
    assert probe_calls == 0


def test_capability_router_success_cache_hit_and_expire_refresh_success():
    clock = _FakeClock(now_epoch=100.0)
    probe_calls = 0

    def _probe(_provider_id: str) -> bool:
        nonlocal probe_calls
        probe_calls += 1
        return True

    router = CapabilityRouter(
        use_responses_api=True,
        supports_responses_probe=_probe,
        success_ttl_seconds=300,
        failure_ttl_seconds=120,
        now_epoch_getter=clock,
    )

    first = router.decide("provider-a")
    assert first.use_responses is True
    assert first.cache_hit is False

    clock.forward(100)
    cached = router.decide("provider-a")
    assert cached.use_responses is True
    assert cached.cache_hit is True

    clock.forward(250)
    refreshed = router.decide("provider-a")
    assert refreshed.use_responses is True
    assert refreshed.cache_hit is False
    assert probe_calls == 2


def test_capability_router_failed_probe_short_ttl_cache_success():
    clock = _FakeClock(now_epoch=0.0)
    probe_calls = 0

    def _probe(_provider_id: str) -> bool:
        nonlocal probe_calls
        probe_calls += 1
        return False

    router = CapabilityRouter(
        use_responses_api=True,
        supports_responses_probe=_probe,
        success_ttl_seconds=300,
        failure_ttl_seconds=120,
        now_epoch_getter=clock,
    )

    first = router.decide("provider-a")
    assert first.use_responses is False
    assert first.fallback_reason_code == FallbackReasonCode.CAPABILITY_UNSUPPORTED
    assert first.cache_hit is False

    clock.forward(60)
    cached = router.decide("provider-a")
    assert cached.use_responses is False
    assert cached.cache_hit is True

    clock.forward(61)
    refreshed = router.decide("provider-a")
    assert refreshed.use_responses is False
    assert refreshed.cache_hit is False
    assert probe_calls == 2


@pytest.mark.parametrize(
    ("error", "expected_code"),
    [
        (TimeoutError("network timeout"), FallbackReasonCode.RESPONSES_TIMEOUT),
        (RuntimeError("429 rate limit"), FallbackReasonCode.RESPONSES_RATE_LIMIT),
        (RuntimeError("500 server error"), FallbackReasonCode.RESPONSES_SERVER_ERROR),
        (RuntimeError("400 bad request"), FallbackReasonCode.RESPONSES_BAD_REQUEST),
        (RuntimeError("501 unsupported"), FallbackReasonCode.CAPABILITY_UNSUPPORTED),
        (RuntimeError("malformed body"), FallbackReasonCode.RESPONSES_PARSE_ERROR),
    ],
)
def test_capability_router_reason_code_mapping_success(error, expected_code):
    assert map_probe_error_to_reason_code(error) == expected_code
