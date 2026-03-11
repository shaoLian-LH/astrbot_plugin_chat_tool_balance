from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from ..pipeline.contracts import NormalizedEvent


def normalize_event(event: Any) -> NormalizedEvent:
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


def is_status_command_message(raw_text: str) -> bool:
    normalized = raw_text.strip().lower()
    return normalized in {"ctb_status", "/ctb_status"}


def _extract_message_text(event: Any) -> str:
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


def _extract_user_id(event: Any) -> str:
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


def _extract_group_id(event: Any) -> str:
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


def _extract_session_id(event: Any, user_id: str, group_id: str) -> str:
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


def _extract_message_id(event: Any) -> str:
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


def _extract_created_at(event: Any) -> str:
    value = getattr(event, "created_at", None)
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, datetime):
        dt = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    return datetime.now(timezone.utc).isoformat()


def _extract_image_urls(event: Any) -> tuple[str, ...]:
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


def _extract_is_bot(event: Any) -> bool:
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


def _extract_conversation_type(event: Any) -> str:
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


def _extract_platform(event: Any) -> str:
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
