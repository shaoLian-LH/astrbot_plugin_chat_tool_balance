from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

DecisionRoute = Literal["tool", "chat"]
TopicRouteSource = Literal["model_classify", "rule_match", "vec_nn", "new_topic"]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    return utc_now().isoformat()


@dataclass(frozen=True)
class ImageFacts:
    source_url: str
    content_hash: str
    source_url_hash: str
    description: str
    metadata: dict[str, Any] = field(default_factory=dict)
    cache_hit: bool = False
    status: str = "ok"

    @property
    def summary(self) -> str:
        return self.description.strip() or "image description unavailable"


@dataclass(frozen=True)
class NormalizedEvent:
    message_id: str
    session_id: str
    scope_id: str
    user_id: str
    text: str = ""
    image_urls: tuple[str, ...] = ()
    role: str = "user"
    created_at: str = field(default_factory=utc_now_iso)
    is_bot: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def iter_non_empty_image_urls(self) -> tuple[str, ...]:
        return tuple(url.strip() for url in self.image_urls if url and url.strip())

    def intent_payload(self, image_facts: tuple[ImageFacts, ...] = ()) -> str:
        text_parts: list[str] = [self.text.strip()]
        for image_fact in image_facts:
            text_parts.append(f"[image] {image_fact.summary}")
        return "\n".join(part for part in text_parts if part)


@dataclass(frozen=True)
class ToolIntentDecision:
    route: DecisionRoute
    confidence: float
    reason_code: str
    model_name: str
    prompt_injection: str = ""

    @property
    def hit(self) -> bool:
        return self.route == "tool"


@dataclass(frozen=True)
class TopicAssignment:
    topic_id: str
    session_id: str
    scope_id: str
    source: TopicRouteSource
    confidence: float
    model_name: str
    title: str
    assigned_at: str = field(default_factory=utc_now_iso)


@dataclass(frozen=True)
class ShortMemoryRecord:
    message_id: str
    scope_id: str
    topic_id: str
    role: str
    content: str
    created_at: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ContextPacket:
    event: NormalizedEvent
    topic: TopicAssignment
    tool_intent: ToolIntentDecision
    image_facts: tuple[ImageFacts, ...]
    short_memory: tuple[ShortMemoryRecord, ...]
    context_messages: tuple[str, ...]
    rendered_context: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class OrchestratorReply:
    message_id: str
    route: DecisionRoute
    reply_text: str
    topic_id: str
    tool_used: bool = False
    fallback_used: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)
