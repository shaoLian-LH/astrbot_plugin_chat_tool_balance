from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from time import sleep
from typing import Any

LivingMemoryClientGetter = Callable[[], Any]
SleepFn = Callable[[float], None]


@dataclass(frozen=True)
class BridgeCallResult:
    success: bool
    attempts: int
    error: str = ""
    response: Any = None


class LivingMemoryV2Bridge:
    """Bridge for LivingMemory v2 with tolerant method adaptation."""

    def __init__(self, client_getter: LivingMemoryClientGetter | None = None) -> None:
        self.client_getter = client_getter or (lambda: None)

    def is_available(self) -> tuple[bool, str]:
        client = self._get_client()
        if client is None:
            return False, "plugin_not_found"

        if hasattr(client, "is_initialized") and callable(client.is_initialized):
            try:
                if not bool(client.is_initialized()):
                    return False, "plugin_not_initialized"
            except Exception as exc:
                return False, f"plugin_init_check_failed:{exc}"
        elif hasattr(client, "initialized") and not bool(getattr(client, "initialized")):
            return False, "plugin_not_initialized"
        return True, "ok"

    def add_memory(
        self,
        scope_id: str,
        topic_id: str,
        summary_text: str,
        metadata: dict[str, Any] | None = None,
    ) -> BridgeCallResult:
        available, reason = self.is_available()
        if not available:
            return BridgeCallResult(success=False, attempts=1, error=reason, response=None)

        client = self._get_client()
        try:
            response = self._call_add_memory(
                client=client,
                scope_id=scope_id,
                topic_id=topic_id,
                summary_text=summary_text,
                metadata=metadata or {},
            )
        except Exception as exc:
            return BridgeCallResult(success=False, attempts=1, error=f"add_memory_failed:{exc}")
        return BridgeCallResult(success=True, attempts=1, response=response)

    def search_memories(self, query: str, limit: int = 5) -> BridgeCallResult:
        available, reason = self.is_available()
        if not available:
            return BridgeCallResult(success=False, attempts=1, error=reason, response=None)

        client = self._get_client()
        try:
            response = self._call_search_memories(client=client, query=query, limit=max(1, int(limit)))
        except Exception as exc:
            return BridgeCallResult(success=False, attempts=1, error=f"search_memories_failed:{exc}")
        return BridgeCallResult(success=True, attempts=1, response=response)

    def sync_summary_with_retry(
        self,
        scope_id: str,
        topic_id: str,
        summary_text: str,
        metadata: dict[str, Any] | None = None,
        max_attempts: int = 3,
        base_delay_seconds: float = 1.0,
        sleep_fn: SleepFn | None = None,
    ) -> BridgeCallResult:
        attempts = max(1, int(max_attempts))
        delay = max(0.0, float(base_delay_seconds))
        wait = sleep_fn or sleep

        last_error = "unknown"
        for attempt in range(1, attempts + 1):
            result = self.add_memory(
                scope_id=scope_id,
                topic_id=topic_id,
                summary_text=summary_text,
                metadata=metadata or {},
            )
            if result.success:
                return BridgeCallResult(success=True, attempts=attempt, response=result.response)
            last_error = result.error or "add_memory_failed"
            if attempt < attempts and delay > 0:
                wait(delay)
                delay *= 2

        return BridgeCallResult(success=False, attempts=attempts, error=last_error)

    def _get_client(self) -> Any:
        try:
            return self.client_getter()
        except Exception:
            return None

    def _call_add_memory(
        self,
        client: Any,
        scope_id: str,
        topic_id: str,
        summary_text: str,
        metadata: dict[str, Any],
    ) -> Any:
        method = None
        for name in ("add_memory", "add_memory_v2"):
            candidate = getattr(client, name, None)
            if callable(candidate):
                method = candidate
                break
        if method is None:
            raise AttributeError("add_memory_missing")

        call_patterns = (
            lambda: method(
                scope_id=scope_id,
                topic_id=topic_id,
                content=summary_text,
                metadata=metadata,
            ),
            lambda: method(scope_id, topic_id, summary_text, metadata),
            lambda: method(summary_text, metadata),
            lambda: method(summary_text),
        )
        return _invoke_adaptively(call_patterns)

    def _call_search_memories(self, client: Any, query: str, limit: int) -> Any:
        method = None
        for name in ("search_memories", "search"):
            candidate = getattr(client, name, None)
            if callable(candidate):
                method = candidate
                break
        if method is None:
            raise AttributeError("search_memories_missing")

        call_patterns = (
            lambda: method(query=query, limit=limit),
            lambda: method(query, limit),
            lambda: method(query),
        )
        return _invoke_adaptively(call_patterns)


def _invoke_adaptively(call_patterns: tuple[Callable[[], Any], ...]) -> Any:
    last_error: Exception | None = None
    for candidate in call_patterns:
        try:
            return candidate()
        except TypeError as exc:
            last_error = exc
            continue
    if last_error is not None:
        raise last_error
    raise RuntimeError("no callable pattern matched")
