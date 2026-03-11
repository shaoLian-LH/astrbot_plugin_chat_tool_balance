from __future__ import annotations

import asyncio
import inspect
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .contracts import ProviderRole


@dataclass(frozen=True)
class ProviderResolution:
    provider_id: str
    source: str


class ProviderResolutionError(RuntimeError):
    def __init__(self, code: str, detail: str = "") -> None:
        message = code if not detail else f"{code}:{detail}"
        super().__init__(message)
        self.code = code
        self.detail = detail


_ASYNC_BRIDGE_TIMEOUT_SECONDS = 5.0


class ProviderResolver:
    def __init__(self, models: Any, runtime_context: Any | None = None) -> None:
        self.models = models
        self.runtime_context = runtime_context

    def resolve_provider(self, provider_role: ProviderRole, event_context: Any = None) -> ProviderResolution:
        role_provider = self._model_provider(provider_role)
        if role_provider:
            return ProviderResolution(provider_id=role_provider, source=f"models.{provider_role}")

        default_provider = self._model_provider("chat_default")
        if default_provider:
            return ProviderResolution(provider_id=default_provider, source="models.chat_default")

        runtime_provider = self._runtime_provider(event_context)
        if runtime_provider:
            return ProviderResolution(
                provider_id=runtime_provider,
                source="runtime.get_current_chat_provider_id",
            )
        raise ProviderResolutionError(
            code="provider_not_configured",
            detail=f"role={provider_role}",
        )

    def resolve_provider_id(self, provider_role: ProviderRole, event_context: Any = None) -> str:
        return self.resolve_provider(provider_role=provider_role, event_context=event_context).provider_id

    def _model_provider(self, field_name: str) -> str:
        model_value = ""
        if isinstance(self.models, Mapping):
            model_value = self.models.get(field_name, "")
        else:
            model_value = getattr(self.models, field_name, "")
        if model_value is None:
            return ""
        return str(model_value).strip()

    def _runtime_provider(self, event_context: Any) -> str:
        getter = getattr(self.runtime_context, "get_current_chat_provider_id", None)
        if not callable(getter):
            return ""

        unified_msg_origin = _read_unified_msg_origin(event_context)
        attempts: list[tuple[tuple[Any, ...], dict[str, Any]]]
        if unified_msg_origin:
            attempts = [
                ((), {"umo": unified_msg_origin}),
                ((unified_msg_origin,), {}),
                ((), {}),
            ]
        else:
            attempts = [((), {}), ((None,), {}), ((), {"umo": None})]
        for args, kwargs in attempts:
            try:
                value = getter(*args, **kwargs)
            except TypeError:
                continue
            except Exception as exc:
                raise ProviderResolutionError(
                    code="provider_runtime_lookup_failed",
                    detail=str(exc),
                ) from exc
            resolved = _resolve_maybe_awaitable(value)
            if resolved:
                return str(resolved).strip()
        return ""


def _read_unified_msg_origin(event_context: Any) -> str:
    if event_context is None:
        return ""
    if isinstance(event_context, Mapping):
        value = event_context.get("unified_msg_origin", "")
        return "" if value is None else str(value).strip()
    value = getattr(event_context, "unified_msg_origin", "")
    if value is None:
        return ""
    return str(value).strip()


def _resolve_maybe_awaitable(value: Any) -> Any:
    if not inspect.isawaitable(value):
        return value
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_await_awaitable(value))
    return _run_awaitable_in_new_thread(value)


async def _await_awaitable(value: Any) -> Any:
    return await value


def _run_awaitable_in_new_thread(value: Any) -> Any:
    state: dict[str, Any] = {}
    done = threading.Event()

    def _runner() -> None:
        try:
            state["result"] = asyncio.run(_await_awaitable(value))
        except Exception as exc:
            state["error"] = exc
        finally:
            done.set()

    bridge_thread = threading.Thread(
        target=_runner,
        name="provider-resolver-async-bridge",
        daemon=True,
    )
    bridge_thread.start()
    if not done.wait(timeout=_ASYNC_BRIDGE_TIMEOUT_SECONDS):
        raise ProviderResolutionError(
            code="provider_runtime_lookup_failed",
            detail="async_bridge_timeout",
        )
    if "error" in state:
        error = state["error"]
        raise ProviderResolutionError(
            code="provider_runtime_lookup_failed",
            detail=str(error),
        ) from error
    return state.get("result")
