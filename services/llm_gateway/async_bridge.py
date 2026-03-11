from __future__ import annotations

import asyncio
import inspect
import threading
from typing import Any

DEFAULT_ASYNC_BRIDGE_TIMEOUT_SECONDS = 20.0


class AsyncBridgeTimeoutError(TimeoutError):
    pass


def run_async_callable_sync(
    callable_obj: Any,
    *args: Any,
    timeout_seconds: float = DEFAULT_ASYNC_BRIDGE_TIMEOUT_SECONDS,
    **kwargs: Any,
) -> Any:
    value = callable_obj(*args, **kwargs)
    if not inspect.isawaitable(value):
        return value
    return run_awaitable_sync(value, timeout_seconds=timeout_seconds)


def run_awaitable_sync(
    awaitable: Any,
    timeout_seconds: float = DEFAULT_ASYNC_BRIDGE_TIMEOUT_SECONDS,
) -> Any:
    timeout_value = _normalize_timeout_seconds(timeout_seconds)
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_await_with_timeout(awaitable, timeout_value))
    return _run_in_thread(awaitable, timeout_seconds=timeout_value)


async def _await_with_timeout(awaitable: Any, timeout_seconds: float) -> Any:
    try:
        return await asyncio.wait_for(awaitable, timeout=timeout_seconds)
    except TimeoutError as exc:
        raise AsyncBridgeTimeoutError("async_bridge_timeout") from exc


def _run_in_thread(awaitable: Any, timeout_seconds: float) -> Any:
    done = threading.Event()
    state: dict[str, Any] = {}

    def _runner() -> None:
        try:
            state["result"] = asyncio.run(_await_with_timeout(awaitable, timeout_seconds))
        except Exception as exc:
            state["error"] = exc
        finally:
            done.set()

    bridge_thread = threading.Thread(
        target=_runner,
        name="llm-gateway-async-bridge",
        daemon=True,
    )
    bridge_thread.start()
    if not done.wait(timeout=timeout_seconds + 0.05):
        raise AsyncBridgeTimeoutError("async_bridge_timeout")
    if "error" in state:
        raise state["error"]
    return state.get("result")


def _normalize_timeout_seconds(timeout_seconds: float) -> float:
    try:
        value = float(timeout_seconds)
    except (TypeError, ValueError):
        return DEFAULT_ASYNC_BRIDGE_TIMEOUT_SECONDS
    if value <= 0:
        return DEFAULT_ASYNC_BRIDGE_TIMEOUT_SECONDS
    return value
