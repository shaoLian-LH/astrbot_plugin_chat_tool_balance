from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

try:
    from astrbot.api import logger
except ModuleNotFoundError:  # pragma: no cover - fallback for local unit tests.
    logger = logging.getLogger(__name__)

from ..bridge.livingmemory_v2_bridge import LivingMemoryV2Bridge
from ..pipeline.contracts import NormalizedEvent, ShortMemoryRecord
from ..pipeline.orchestrator import ChatToolBalanceOrchestrator
from ..plugin_config import ModelSettings, PluginSettings, load_plugin_settings
from ..scheduler.summary_executor import SummaryExecutor
from ..scheduler.summary_state_janitor import SummaryStateJanitor
from .llm_gateway import (
    AstrBotTransport,
    CapabilityRouter,
    GenerateSyncRequest,
    GatewayMetricsRecorder,
    GatewayResult,
    LLMGateway,
    ProviderResolver,
    ResponsesTransport,
)
from ..storage import ResponseStateRepository
from ..storage.bootstrap import StorageBootstrapResult, initialize_storage
from ..storage.path_manager import StoragePathManager


@dataclass(frozen=True)
class RuntimeWiringResult:
    settings: PluginSettings
    storage_bootstrap: StorageBootstrapResult
    llm_gateway: LLMGateway
    livingmemory_bridge: LivingMemoryV2Bridge
    summary_executor: SummaryExecutor
    orchestrator: ChatToolBalanceOrchestrator


def build_runtime_wiring(context: Any, plugin: Any) -> RuntimeWiringResult:
    raw_config = extract_raw_plugin_config(context, plugin)
    settings = load_plugin_settings(raw_config)
    storage_bootstrap = initialize_storage(
        base_dir=settings.storage.base_dir,
        bucket_count=settings.storage.bucket_count,
    )
    path_manager = storage_bootstrap.path_manager
    llm_gateway = build_llm_gateway(
        settings=settings,
        path_manager=path_manager,
        runtime_context=context,
    )
    livingmemory_bridge = LivingMemoryV2Bridge(client_getter=build_livingmemory_client_getter(context))
    summary_executor = SummaryExecutor(
        path_manager=path_manager,
        summary_model_name=settings.models.summary,
        summary_generator=build_summary_gateway_generator(llm_gateway=llm_gateway),
        bridge=livingmemory_bridge,
        summary_state_janitor=llm_gateway.summary_state_janitor,
    )
    orchestrator = ChatToolBalanceOrchestrator(
        settings=settings,
        path_manager=path_manager,
        summary_executor=summary_executor,
        llm_gateway=llm_gateway,
    )
    wire_non_chat_gateway_paths(
        orchestrator=orchestrator,
        llm_gateway=llm_gateway,
    )
    return RuntimeWiringResult(
        settings=settings,
        storage_bootstrap=storage_bootstrap,
        llm_gateway=llm_gateway,
        livingmemory_bridge=livingmemory_bridge,
        summary_executor=summary_executor,
        orchestrator=orchestrator,
    )


def build_llm_gateway(
    settings: PluginSettings,
    path_manager: StoragePathManager,
    runtime_context: Any,
) -> LLMGateway:
    metrics_recorder = GatewayMetricsRecorder()
    model_name_resolver = build_gateway_model_name_resolver(settings.models)
    provider_resolver = ProviderResolver(models=settings.models, runtime_context=runtime_context)
    capability_router = CapabilityRouter(use_responses_api=settings.features.use_responses_api)
    responses_transport = ResponsesTransport()
    astrbot_transport = AstrBotTransport(runtime_context=runtime_context)
    state_repository = ResponseStateRepository(path_manager=path_manager)
    summary_state_janitor = SummaryStateJanitor(
        state_repository=state_repository,
        metrics_recorder=metrics_recorder,
    )
    return LLMGateway(
        provider_resolver=provider_resolver,
        capability_router=capability_router,
        responses_transport=responses_transport,
        astrbot_transport=astrbot_transport,
        state_repository=state_repository,
        summary_state_janitor=summary_state_janitor,
        metrics_recorder=metrics_recorder,
        model_name_resolver=model_name_resolver,
    )


def build_gateway_model_name_resolver(models: ModelSettings) -> Callable[[str, str], str]:
    default_model_name = str(models.chat_model or "").strip()
    role_to_model = {
        "chat": default_model_name,
        "ocr": str(models.ocr_model or "").strip(),
        "topic_classifier": str(models.topic_classifier_model or "").strip(),
        "tool_intent_classifier": str(models.tool_intent_classifier_model or "").strip(),
        "summary": str(models.summary_model or "").strip(),
    }

    def _resolve(role: str, provider_id: str) -> str:
        role_key = str(role or "").strip()
        if role_key not in role_to_model:
            return str(provider_id or "").strip()
        configured_model = role_to_model.get(role_key, "")
        if configured_model:
            return configured_model
        if default_model_name:
            return default_model_name
        return str(provider_id or "").strip()

    return _resolve


def wire_non_chat_gateway_paths(orchestrator: ChatToolBalanceOrchestrator, llm_gateway: LLMGateway) -> None:
    orchestrator.image_stage.describe_image = build_ocr_gateway_describer(llm_gateway=llm_gateway)
    orchestrator.tool_intent_stage.classifier = build_tool_intent_gateway_classifier(
        llm_gateway=llm_gateway
    )
    orchestrator.topic_router_stage.classifier = build_topic_gateway_classifier(llm_gateway=llm_gateway)


def build_ocr_gateway_describer(
    llm_gateway: LLMGateway,
) -> Callable[[str, NormalizedEvent], tuple[str, dict[str, Any]]]:
    def _describe(source_url: str, event: NormalizedEvent) -> tuple[str, dict[str, Any]]:
        result = llm_gateway.generate_once_sync(
            GenerateSyncRequest(
                provider_role="ocr",
                instructions=(
                    "你是 OCR 图像描述助手。请根据图片 URL 生成一句简洁中文描述，"
                    "不要输出 JSON。"
                ),
                input={
                    "image_url": source_url,
                    "user_text": event.text,
                },
                metadata={
                    "message_id": event.message_id,
                    "scope_id": event.scope_id,
                    "session_id": event.session_id,
                },
                event_context=event.metadata,
            )
        )
        description = str(result.text or "").strip()
        if not description:
            raise RuntimeError("ocr_empty_result")
        return description, _gateway_metadata(result=result)

    return _describe


def build_tool_intent_gateway_classifier(
    llm_gateway: LLMGateway,
) -> Callable[[str, str, NormalizedEvent], tuple[float, str]]:
    def _classifier(payload: str, model_name: str, event: NormalizedEvent) -> tuple[float, str]:
        result = llm_gateway.generate_once_sync(
            GenerateSyncRequest(
                provider_role="tool_intent_classifier",
                instructions=(
                    "你是工具意图分类器。仅输出 JSON："
                    '{"route":"tool|chat","confidence":0~1,"reason":"短原因"}。'
                ),
                input={
                    "payload": payload,
                    "model_name": model_name,
                },
                metadata={"stage": "tool_intent"},
                event_context=event.metadata,
            )
        )
        parsed = _parse_json_object(result.text)
        if parsed is None:
            raise ValueError("tool_intent_parse_failed")
        route = str(parsed.get("route", "")).strip().lower()
        confidence = _as_float(parsed.get("confidence"), default=0.5)
        if route == "tool":
            confidence = max(confidence, 0.7)
        elif route == "chat":
            confidence = min(confidence, 0.3)
        else:
            raise ValueError("tool_intent_route_invalid")
        reason = str(parsed.get("reason", "")).strip() or "gateway_structured"
        return confidence, reason

    return _classifier


def build_topic_gateway_classifier(
    llm_gateway: LLMGateway,
) -> Callable[[NormalizedEvent, str], tuple[str | None, float] | str | None]:
    def _classifier(event: NormalizedEvent, model_name: str) -> tuple[str | None, float] | str | None:
        result = llm_gateway.generate_once_sync(
            GenerateSyncRequest(
                provider_role="topic_classifier",
                instructions=(
                    "你是主题路由分类器。仅输出 JSON："
                    '{"topic_id":"可复用主题ID或空字符串","confidence":0~1}。'
                ),
                input={
                    "text": event.text,
                    "scope_id": event.scope_id,
                    "session_id": event.session_id,
                    "model_name": model_name,
                },
                metadata={"stage": "topic_router", "message_id": event.message_id},
                event_context=event.metadata,
            )
        )
        parsed = _parse_json_object(result.text)
        if parsed is None:
            raise ValueError("topic_classifier_parse_failed")
        topic_id = str(parsed.get("topic_id", "")).strip()
        if not topic_id:
            return None
        confidence = _as_float(parsed.get("confidence"), default=0.8)
        return topic_id, confidence

    return _classifier


def build_summary_gateway_generator(
    llm_gateway: LLMGateway,
) -> Callable[[tuple[ShortMemoryRecord, ...], str], tuple[str, float]]:
    def _generate(records: tuple[ShortMemoryRecord, ...], model_name: str) -> tuple[str, float]:
        payload = [
            {
                "role": item.role,
                "content": item.content,
                "message_id": item.message_id,
            }
            for item in records
        ]
        result = llm_gateway.generate_once_sync(
            GenerateSyncRequest(
                provider_role="summary",
                instructions=(
                    "你是对话总结助手。优先输出 JSON："
                    '{"summary":"总结内容","quality":0~1}；如果不能输出 JSON，直接输出总结文本。'
                ),
                input={
                    "messages": payload,
                    "model_name": model_name,
                },
                metadata={"stage": "summary_executor", "message_count": len(records)},
            )
        )
        summary_text = str(result.text or "").strip()
        parsed = _parse_json_object(summary_text)
        if parsed is not None:
            parsed_summary = str(parsed.get("summary", "")).strip()
            if parsed_summary:
                return parsed_summary, _as_float(parsed.get("quality"), default=0.82)
        if summary_text:
            fallback_quality = min(0.95, 0.55 + 0.02 * len(records))
            return summary_text, fallback_quality
        raise RuntimeError("summary_empty_result")

    return _generate


def _gateway_metadata(result: GatewayResult) -> dict[str, Any]:
    return {
        "provider": "llm_gateway",
        "transport_used": result.transport_used,
        "provider_id": result.provider_id,
        "model_name": result.model_name,
        "response_id": result.response_id or "",
        "fallback_reason_code": _fallback_reason_value(result),
    }


def _fallback_reason_value(result: GatewayResult) -> str:
    reason = result.fallback_reason_code
    if reason is None:
        return ""
    return str(getattr(reason, "value", reason))


def _parse_json_object(raw_text: str) -> dict[str, Any] | None:
    text = str(raw_text or "").strip()
    if not text:
        return None
    candidates = [text]
    if "```" in text:
        stripped = text.replace("```json", "```").replace("```JSON", "```")
        chunks = [chunk.strip() for chunk in stripped.split("```") if chunk.strip()]
        candidates.extend(chunks)
    left = text.find("{")
    right = text.rfind("}")
    if left >= 0 and right > left:
        candidates.append(text[left : right + 1])

    for candidate in candidates:
        try:
            loaded = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(loaded, dict):
            return loaded
    return None


def _as_float(value: Any, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, parsed))


def extract_raw_plugin_config(context: Any, plugin: Any) -> Mapping[str, Any]:
    candidates: list[Any] = [
        getattr(plugin, "config", None),
        getattr(context, "config", None),
    ]
    for candidate in candidates:
        if isinstance(candidate, Mapping):
            return candidate

    for getter_name in ("get_config", "get_plugin_config", "get_plugin_conf"):
        getter = getattr(context, getter_name, None)
        if not callable(getter):
            continue
        for args in ((), ("astrbot_plugin_chat_tool_balance",), ("chat_tool_balance",)):
            try:
                candidate = getter(*args)
            except TypeError:
                continue
            except Exception as exc:  # pragma: no cover - depends on runtime context.
                logger.warning("读取插件配置失败: getter=%s, err=%s", getter_name, exc)
                break
            if isinstance(candidate, Mapping):
                return candidate
    return {}


def build_livingmemory_client_getter(context: Any) -> Callable[[], Any]:
    plugin_keys = (
        "livingmemory_v2",
        "livingmemory",
        "astrbot_plugin_livingmemory_v2",
        "astrbot_plugin_livingmemory",
    )
    getter_specs = (
        ("get_plugin", plugin_keys),
        ("get_plugin_by_name", plugin_keys),
        ("get_star", plugin_keys),
        ("get_service", plugin_keys),
    )

    def _resolve() -> Any:
        for getter_name, names in getter_specs:
            getter = getattr(context, getter_name, None)
            if not callable(getter):
                continue
            for name in names:
                try:
                    candidate = getter(name)
                except Exception:
                    continue
                if candidate is not None:
                    return candidate

        plugin_manager = getattr(context, "plugin_manager", None)
        if plugin_manager is not None:
            for getter_name, names in getter_specs:
                getter = getattr(plugin_manager, getter_name, None)
                if not callable(getter):
                    continue
                for name in names:
                    try:
                        candidate = getter(name)
                    except Exception:
                        continue
                    if candidate is not None:
                        return candidate
        return None

    return _resolve
