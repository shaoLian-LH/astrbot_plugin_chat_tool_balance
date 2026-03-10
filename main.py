from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

from bridge.livingmemory_v2_bridge import LivingMemoryV2Bridge
from pipeline.contracts import NormalizedEvent
from pipeline.orchestrator import ChatToolBalanceOrchestrator
from plugin_config import PluginSettings, load_plugin_settings
from scheduler.summary_executor import SummaryExecutor
from storage.bootstrap import StorageBootstrapResult, initialize_storage


def _extract_raw_plugin_config(context: Context, plugin: Star) -> Mapping[str, Any]:
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
                logger.warning(f"读取插件配置失败: getter={getter_name}, err={exc}")
                break
            if isinstance(candidate, Mapping):
                return candidate
    return {}


def _build_livingmemory_client_getter(context: Context):
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


def _resolve_event_decorator():
    event_message_type = getattr(filter, "event_message_type", None)
    if not callable(event_message_type):
        return lambda fn: fn
    event_type_enum = getattr(filter, "EventMessageType", None)
    event_all = getattr(event_type_enum, "ALL", "all")
    return event_message_type(event_all)


_on_event_message = _resolve_event_decorator()


def _normalize_event(event: AstrMessageEvent) -> NormalizedEvent:
    text = _extract_message_text(event)
    user_id = _extract_user_id(event)
    group_id = _extract_group_id(event)
    session_id = _extract_session_id(event, user_id=user_id, group_id=group_id)
    scope_id = f"group:{group_id}" if group_id else f"private:{user_id}"
    message_id = _extract_message_id(event)
    created_at = _extract_created_at(event)
    image_urls = _extract_image_urls(event)
    is_bot = _extract_is_bot(event)
    return NormalizedEvent(
        message_id=message_id,
        session_id=session_id,
        scope_id=scope_id,
        user_id=user_id,
        text=text,
        image_urls=image_urls,
        role="assistant" if is_bot else "user",
        created_at=created_at,
        is_bot=is_bot,
        metadata={
            "conversation_type": _extract_conversation_type(event),
            "group_id": group_id or "",
            "platform": _extract_platform(event),
            "unified_msg_origin": str(getattr(event, "unified_msg_origin", "") or ""),
        },
    )


def _extract_message_text(event: AstrMessageEvent) -> str:
    for attr_name in ("message_str", "text", "message"):
        value = getattr(event, attr_name, None)
        if isinstance(value, str) and value.strip():
            return value.strip()
    for getter_name in ("get_message_str", "get_text", "get_plain_text"):
        getter = getattr(event, getter_name, None)
        if not callable(getter):
            continue
        try:
            value = getter()
        except Exception:
            continue
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _extract_user_id(event: AstrMessageEvent) -> str:
    for attr_name in ("sender_id", "user_id", "from_user_id"):
        value = getattr(event, attr_name, None)
        if value is not None and str(value).strip():
            return str(value).strip()
    for getter_name in ("get_sender_id", "get_user_id"):
        getter = getattr(event, getter_name, None)
        if not callable(getter):
            continue
        try:
            value = getter()
        except Exception:
            continue
        if value is not None and str(value).strip():
            return str(value).strip()
    return "unknown_user"


def _extract_group_id(event: AstrMessageEvent) -> str:
    for attr_name in ("group_id", "chat_id"):
        value = getattr(event, attr_name, None)
        if value is not None and str(value).strip():
            return str(value).strip()
    getter = getattr(event, "get_group_id", None)
    if callable(getter):
        try:
            value = getter()
        except Exception:
            value = None
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _extract_session_id(event: AstrMessageEvent, user_id: str, group_id: str) -> str:
    for attr_name in ("session_id", "conversation_id", "unified_msg_origin"):
        value = getattr(event, attr_name, None)
        if value is not None and str(value).strip():
            return str(value).strip()
    for getter_name in ("get_session_id", "get_conversation_id"):
        getter = getattr(event, getter_name, None)
        if not callable(getter):
            continue
        try:
            value = getter()
        except Exception:
            continue
        if value is not None and str(value).strip():
            return str(value).strip()
    if group_id:
        return f"group:{group_id}"
    return f"private:{user_id}"


def _extract_message_id(event: AstrMessageEvent) -> str:
    for attr_name in ("message_id", "msg_id", "id"):
        value = getattr(event, attr_name, None)
        if value is not None and str(value).strip():
            return str(value).strip()
    for getter_name in ("get_message_id", "get_msg_id"):
        getter = getattr(event, getter_name, None)
        if not callable(getter):
            continue
        try:
            value = getter()
        except Exception:
            continue
        if value is not None and str(value).strip():
            return str(value).strip()
    return f"msg_{uuid4().hex[:16]}"


def _extract_created_at(event: AstrMessageEvent) -> str:
    value = getattr(event, "created_at", None)
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, datetime):
        dt = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    return datetime.now(timezone.utc).isoformat()


def _extract_image_urls(event: AstrMessageEvent) -> tuple[str, ...]:
    urls: list[str] = []
    for attr_name in ("image_urls", "images"):
        value = getattr(event, attr_name, None)
        _collect_urls(urls, value)

    get_extra = getattr(event, "get_extra", None)
    if callable(get_extra):
        try:
            _collect_urls(urls, get_extra("image_urls", []))
        except Exception:
            pass

    get_messages = getattr(event, "get_messages", None)
    if callable(get_messages):
        try:
            messages = get_messages()
        except Exception:
            messages = []
        if isinstance(messages, (list, tuple)):
            for part in messages:
                if isinstance(part, Mapping):
                    part_type = str(part.get("type", "")).lower()
                    if "image" in part_type or "img" in part_type:
                        _collect_urls(urls, part.get("url") or part.get("src") or part.get("file"))
                else:
                    part_type = str(getattr(part, "type", "")).lower()
                    class_name = part.__class__.__name__.lower()
                    if "image" in part_type or "img" in part_type or "image" in class_name:
                        _collect_urls(
                            urls,
                            getattr(part, "url", None)
                            or getattr(part, "src", None)
                            or getattr(part, "file", None),
                        )

    seen: set[str] = set()
    deduped: list[str] = []
    for item in urls:
        normalized = item.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
    return tuple(deduped)


def _collect_urls(target: list[str], value: Any) -> None:
    if value is None:
        return
    if isinstance(value, str):
        if value.strip():
            target.append(value.strip())
        return
    if isinstance(value, Mapping):
        _collect_urls(target, value.get("url") or value.get("src") or value.get("file"))
        return
    if isinstance(value, (list, tuple, set)):
        for item in value:
            _collect_urls(target, item)


def _extract_is_bot(event: AstrMessageEvent) -> bool:
    for attr_name in ("is_bot", "is_self", "from_bot"):
        value = getattr(event, attr_name, None)
        if isinstance(value, bool):
            return value
    get_extra = getattr(event, "get_extra", None)
    if callable(get_extra):
        for key in ("is_bot", "is_self", "from_bot"):
            try:
                value = get_extra(key, None)
            except Exception:
                continue
            if isinstance(value, bool):
                return value
    return False


def _extract_conversation_type(event: AstrMessageEvent) -> str:
    value = getattr(event, "conversation_type", None)
    if isinstance(value, str) and value.strip():
        return value.strip().lower()
    getter = getattr(event, "get_conversation_type", None)
    if callable(getter):
        try:
            result = getter()
        except Exception:
            result = None
        if isinstance(result, str) and result.strip():
            return result.strip().lower()
    return "group" if _extract_group_id(event) else "private"


def _extract_platform(event: AstrMessageEvent) -> str:
    value = getattr(event, "platform", None)
    if isinstance(value, str) and value.strip():
        return value.strip()
    getter = getattr(event, "get_platform", None)
    if callable(getter):
        try:
            result = getter()
        except Exception:
            result = None
        if isinstance(result, str) and result.strip():
            return result.strip()
    return ""


def _is_status_command_message(raw_text: str) -> bool:
    normalized = raw_text.strip().lower()
    return normalized in {"ctb_status", "/ctb_status"}


@register("chat_tool_balance", "shaoLian-LH", "平衡聊天与工具调用插件", "v0.1.0")
class ChatToolBalancePlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self.settings: PluginSettings = load_plugin_settings({})
        self.storage_bootstrap: StorageBootstrapResult | None = None
        self.orchestrator: ChatToolBalanceOrchestrator | None = None
        self.livingmemory_bridge: LivingMemoryV2Bridge | None = None
        self.summary_executor: SummaryExecutor | None = None

    async def initialize(self):
        raw_config = _extract_raw_plugin_config(self.context, self)
        self.settings = load_plugin_settings(raw_config)
        self.storage_bootstrap = initialize_storage(
            base_dir=self.settings.storage.base_dir,
            bucket_count=self.settings.storage.bucket_count,
        )
        path_manager = self.storage_bootstrap.path_manager
        self.livingmemory_bridge = LivingMemoryV2Bridge(
            client_getter=_build_livingmemory_client_getter(self.context)
        )
        self.summary_executor = SummaryExecutor(
            path_manager=path_manager,
            summary_model_name=self.settings.models.summary,
            bridge=self.livingmemory_bridge,
        )
        self.orchestrator = ChatToolBalanceOrchestrator(
            settings=self.settings,
            path_manager=path_manager,
            summary_executor=self.summary_executor,
        )
        lm_available, lm_reason = self.livingmemory_bridge.is_available()
        logger.info(
            "chat_tool_balance initialized, base_dir=%s, bucket_count=%s, lm_available=%s, lm_reason=%s",
            self.settings.storage.base_dir,
            self.settings.storage.bucket_count,
            lm_available,
            lm_reason,
        )

    @filter.command("ctb_status")
    async def ctb_status(self, event: AstrMessageEvent):
        if self.orchestrator is None or self.storage_bootstrap is None:
            yield event.plain_result("chat_tool_balance: 未初始化")
            return
        yield event.plain_result(
            "chat_tool_balance: ready "
            f"(base_dir={self.settings.storage.base_dir}, "
            f"bucket_count={self.settings.storage.bucket_count})"
        )

    @_on_event_message
    async def on_event_message(self, event: AstrMessageEvent):
        if self.orchestrator is None:
            return
        normalized_event = _normalize_event(event)
        if normalized_event.is_bot:
            return
        if not normalized_event.text and not normalized_event.image_urls:
            return
        if _is_status_command_message(normalized_event.text):
            return
        try:
            reply = self.orchestrator.handle_event(normalized_event)
        except Exception as exc:
            logger.error("chat_tool_balance message handling failed: %s", exc)
            return
        reply_text = (reply.reply_text or "").strip()
        if not reply_text:
            return
        yield event.plain_result(reply_text)

    async def terminate(self):
        self.orchestrator = None
        self.summary_executor = None
        self.livingmemory_bridge = None
