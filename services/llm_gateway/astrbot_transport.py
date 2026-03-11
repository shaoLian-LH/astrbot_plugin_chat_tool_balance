from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from services.llm_gateway.async_bridge import AsyncBridgeTimeoutError, run_async_callable_sync
from services.llm_gateway.contracts import FallbackReasonCode, GatewayResult


@dataclass(frozen=True)
class AstrBotTransportRequest:
    provider_id: str
    instructions: str
    input: Any
    metadata: Mapping[str, Any] = field(default_factory=dict)
    event_context: Any = None
    model_name: str = ""
    use_tool_loop_agent: bool = False


class AstrBotTransportError(RuntimeError):
    def __init__(self, detail: str = "") -> None:
        message = FallbackReasonCode.FALLBACK_FAILED.value
        if detail:
            message = f"{message}:{detail}"
        super().__init__(message)
        self.reason_code = FallbackReasonCode.FALLBACK_FAILED
        self.detail = detail


class AstrBotTransport:
    def __init__(
        self,
        runtime_context: Any,
        timeout_seconds: float = 20.0,
    ) -> None:
        self.runtime_context = runtime_context
        self.timeout_seconds = timeout_seconds

    def generate(self, request: AstrBotTransportRequest) -> GatewayResult:
        try:
            result = self._invoke_runtime(request)
        except AstrBotTransportError:
            raise
        except AsyncBridgeTimeoutError as exc:
            raise AstrBotTransportError("timeout") from exc
        except Exception as exc:
            raise AstrBotTransportError(str(exc)) from exc

        text = _extract_text(result)
        return GatewayResult(
            text=text,
            transport_used="fallback_chat",
            provider_id=str(request.provider_id or "").strip(),
            model_name=str(request.model_name or "").strip() or str(request.provider_id or "").strip(),
        )

    def _invoke_runtime(self, request: AstrBotTransportRequest) -> Any:
        if request.use_tool_loop_agent:
            tool_loop = getattr(self.runtime_context, "tool_loop_agent", None)
            if callable(tool_loop):
                return self._call_tool_loop(tool_loop, request)
        llm_generate = getattr(self.runtime_context, "llm_generate", None)
        if not callable(llm_generate):
            raise AstrBotTransportError("llm_generate_unavailable")
        return self._call_llm_generate(llm_generate, request)

    def _call_llm_generate(self, llm_generate: Any, request: AstrBotTransportRequest) -> Any:
        provider_id = str(request.provider_id or "").strip()
        prompt = _build_prompt(request.instructions, request.input)
        contexts = _build_contexts(request.input)
        event_context = request.event_context

        attempts = (
            {
                "chat_provider_id": provider_id,
                "prompt": prompt,
                "contexts": contexts,
                "event": event_context,
            },
            {
                "chat_provider_id": provider_id,
                "prompt": prompt,
                "contexts": contexts,
            },
            {
                "chat_provider_id": provider_id,
                "prompt": prompt,
            },
            {
                "provider_id": provider_id,
                "prompt": prompt,
            },
            {
                "model": provider_id,
                "prompt": prompt,
            },
            {"prompt": prompt},
        )
        for kwargs in attempts:
            sanitized = {key: value for key, value in kwargs.items() if value is not None}
            try:
                return run_async_callable_sync(
                    llm_generate,
                    timeout_seconds=self.timeout_seconds,
                    **sanitized,
                )
            except TypeError:
                continue
        try:
            return run_async_callable_sync(
                llm_generate,
                prompt,
                timeout_seconds=self.timeout_seconds,
            )
        except TypeError as exc:
            raise AstrBotTransportError("llm_generate_signature_mismatch") from exc

    def _call_tool_loop(self, tool_loop_agent: Any, request: AstrBotTransportRequest) -> Any:
        provider_id = str(request.provider_id or "").strip()
        prompt = _build_prompt(request.instructions, request.input)
        attempts = (
            {"chat_provider_id": provider_id, "prompt": prompt},
            {"provider_id": provider_id, "prompt": prompt},
            {"prompt": prompt},
        )
        for kwargs in attempts:
            try:
                return run_async_callable_sync(
                    tool_loop_agent,
                    timeout_seconds=self.timeout_seconds,
                    **kwargs,
                )
            except TypeError:
                continue
        raise AstrBotTransportError("tool_loop_signature_mismatch")


def _build_prompt(instructions: str, input_payload: Any) -> str:
    instruction_text = str(instructions or "").strip()
    input_text = _stringify_input(input_payload)
    if instruction_text and input_text:
        return f"{instruction_text}\n\n{input_text}"
    if instruction_text:
        return instruction_text
    return input_text


def _build_contexts(input_payload: Any) -> tuple[str, ...]:
    if isinstance(input_payload, tuple):
        return tuple(str(item) for item in input_payload)
    if isinstance(input_payload, list):
        return tuple(str(item) for item in input_payload)
    input_text = _stringify_input(input_payload)
    if not input_text:
        return ()
    return (input_text,)


def _stringify_input(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, Mapping):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    if isinstance(value, (list, tuple)):
        return "\n".join(str(item) for item in value)
    return str(value)


def _extract_text(result: Any) -> str:
    if result is None:
        return ""
    if isinstance(result, str):
        return result.strip()
    if isinstance(result, Mapping):
        for key in ("text", "output_text", "content", "message"):
            value = result.get(key)
            if isinstance(value, str):
                return value.strip()
            if value is not None:
                return str(value).strip()
    for attr in ("text", "output_text", "content", "message"):
        value = getattr(result, attr, None)
        if isinstance(value, str):
            return value.strip()
        if value is not None:
            return str(value).strip()
    return str(result).strip()
