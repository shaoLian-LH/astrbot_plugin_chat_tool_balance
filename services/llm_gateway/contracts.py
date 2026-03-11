from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal
from collections.abc import Mapping

ProviderRole = Literal["chat", "ocr", "topic_classifier", "tool_intent_classifier", "summary"]
GenerateProviderRole = Literal["ocr", "topic_classifier", "tool_intent_classifier", "summary"]
TransportUsed = Literal["responses", "fallback_chat"]


class FallbackReasonCode(str, Enum):
    CAPABILITY_UNSUPPORTED = "E_CAPABILITY_UNSUPPORTED"
    RESPONSES_TIMEOUT = "E_RESPONSES_TIMEOUT"
    RESPONSES_RATE_LIMIT = "E_RESPONSES_RATE_LIMIT"
    RESPONSES_SERVER_ERROR = "E_RESPONSES_SERVER_ERROR"
    RESPONSES_BAD_REQUEST = "E_RESPONSES_BAD_REQUEST"
    RESPONSES_PARSE_ERROR = "E_RESPONSES_PARSE_ERROR"
    FALLBACK_FAILED = "E_FALLBACK_FAILED"


@dataclass(frozen=True)
class ChatSyncRequest:
    scope_id: str
    topic_id: str
    instructions: str
    input: Any
    metadata: Mapping[str, Any] = field(default_factory=dict)
    event_context: Any = None
    provider_role: Literal["chat"] = "chat"


@dataclass(frozen=True)
class GenerateSyncRequest:
    provider_role: GenerateProviderRole
    instructions: str
    input: Any
    metadata: Mapping[str, Any] = field(default_factory=dict)
    event_context: Any = None


@dataclass(frozen=True)
class GatewayResult:
    text: str
    transport_used: TransportUsed
    provider_id: str = ""
    model_name: str = ""
    response_id: str | None = None
    fallback_reason_code: FallbackReasonCode | None = None
    usage: Mapping[str, Any] | None = None


ChatSyncResult = GatewayResult
GenerateSyncResult = GatewayResult

