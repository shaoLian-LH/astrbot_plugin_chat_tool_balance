from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from collections.abc import Callable

from services.llm_gateway.contracts import FallbackReasonCode


@dataclass(frozen=True)
class CapabilityDecision:
    provider_id: str
    use_responses: bool
    fallback_reason_code: FallbackReasonCode | None = None
    cache_hit: bool = False


@dataclass
class _CapabilityCacheEntry:
    supports_responses: bool
    fallback_reason_code: FallbackReasonCode | None
    checked_at_epoch: float
    ttl_seconds: int


class CapabilityRouter:
    def __init__(
        self,
        use_responses_api: bool = True,
        supports_responses_probe: Callable[[str], bool] | None = None,
        success_ttl_seconds: int = 15 * 60,
        failure_ttl_seconds: int = 2 * 60,
        now_epoch_getter: Callable[[], float] | None = None,
    ) -> None:
        self.use_responses_api = bool(use_responses_api)
        self.supports_responses_probe = supports_responses_probe or _default_supports_responses_probe
        self.success_ttl_seconds = max(1, int(success_ttl_seconds))
        self.failure_ttl_seconds = max(1, int(failure_ttl_seconds))
        self.now_epoch_getter = now_epoch_getter or _default_now_epoch
        self._cache: dict[str, _CapabilityCacheEntry] = {}

    def clear_cache(self) -> None:
        self._cache.clear()

    def decide(self, provider_id: str) -> CapabilityDecision:
        normalized_provider_id = str(provider_id or "").strip()
        if not normalized_provider_id:
            return CapabilityDecision(
                provider_id=normalized_provider_id,
                use_responses=False,
                fallback_reason_code=FallbackReasonCode.CAPABILITY_UNSUPPORTED,
            )
        if not self.use_responses_api:
            return CapabilityDecision(
                provider_id=normalized_provider_id,
                use_responses=False,
                fallback_reason_code=FallbackReasonCode.CAPABILITY_UNSUPPORTED,
            )

        now_epoch = self.now_epoch_getter()
        cached = self._cache.get(normalized_provider_id)
        if cached is not None and now_epoch - cached.checked_at_epoch < cached.ttl_seconds:
            return CapabilityDecision(
                provider_id=normalized_provider_id,
                use_responses=cached.supports_responses,
                fallback_reason_code=None
                if cached.supports_responses
                else (cached.fallback_reason_code or FallbackReasonCode.CAPABILITY_UNSUPPORTED),
                cache_hit=True,
            )

        try:
            supports_responses = bool(self.supports_responses_probe(normalized_provider_id))
            reason_code = None if supports_responses else FallbackReasonCode.CAPABILITY_UNSUPPORTED
        except Exception as exc:
            supports_responses = False
            reason_code = map_probe_error_to_reason_code(exc)

        ttl_seconds = self.success_ttl_seconds if supports_responses else self.failure_ttl_seconds
        self._cache[normalized_provider_id] = _CapabilityCacheEntry(
            supports_responses=supports_responses,
            fallback_reason_code=reason_code,
            checked_at_epoch=now_epoch,
            ttl_seconds=ttl_seconds,
        )
        return CapabilityDecision(
            provider_id=normalized_provider_id,
            use_responses=supports_responses,
            fallback_reason_code=reason_code,
            cache_hit=False,
        )

    def supports_responses(self, provider_id: str) -> bool:
        return self.decide(provider_id).use_responses


def map_probe_error_to_reason_code(error: Exception) -> FallbackReasonCode:
    if isinstance(error, TimeoutError):
        return FallbackReasonCode.RESPONSES_TIMEOUT
    name = type(error).__name__.lower()
    message = str(error).lower()
    combined = f"{name}:{message}"

    if "timeout" in combined:
        return FallbackReasonCode.RESPONSES_TIMEOUT
    if "rate" in combined and "limit" in combined:
        return FallbackReasonCode.RESPONSES_RATE_LIMIT
    if "429" in combined:
        return FallbackReasonCode.RESPONSES_RATE_LIMIT
    if "unsupported" in combined or "not implemented" in combined:
        return FallbackReasonCode.CAPABILITY_UNSUPPORTED
    if "404" in combined or "501" in combined:
        return FallbackReasonCode.CAPABILITY_UNSUPPORTED
    if "400" in combined or "bad request" in combined:
        return FallbackReasonCode.RESPONSES_BAD_REQUEST
    if "500" in combined or "502" in combined or "503" in combined or "504" in combined:
        return FallbackReasonCode.RESPONSES_SERVER_ERROR
    if "server error" in combined:
        return FallbackReasonCode.RESPONSES_SERVER_ERROR
    return FallbackReasonCode.RESPONSES_PARSE_ERROR


def _default_supports_responses_probe(_provider_id: str) -> bool:
    return True


def _default_now_epoch() -> float:
    return datetime.now(timezone.utc).timestamp()

