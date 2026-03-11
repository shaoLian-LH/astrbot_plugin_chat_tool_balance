from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from services.llm_gateway.client_factory import ResponsesClientFactory
from services.llm_gateway.contracts import FallbackReasonCode, GatewayResult


@dataclass(frozen=True)
class ResponsesTransportRequest:
    provider_id: str
    model_name: str
    instructions: str
    input: Any
    previous_response_id: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ResponsesTransportAggregate:
    text: str
    response_id: str | None
    model_name: str
    usage: Mapping[str, Any] | None


class ResponsesTransportError(RuntimeError):
    def __init__(
        self,
        reason_code: FallbackReasonCode,
        detail: str = "",
    ) -> None:
        message = reason_code.value if not detail else f"{reason_code.value}:{detail}"
        super().__init__(message)
        self.reason_code = reason_code
        self.detail = detail


class ResponsesTransport:
    def __init__(self, client_factory: ResponsesClientFactory | None = None) -> None:
        self.client_factory = client_factory or ResponsesClientFactory()

    def generate(self, request: ResponsesTransportRequest) -> GatewayResult:
        provider_id = str(request.provider_id or "").strip()
        model_name = str(request.model_name or "").strip() or provider_id
        factory_result = self.client_factory.create_client(provider_id)
        if not factory_result.ok:
            raise ResponsesTransportError(
                reason_code=factory_result.reason_code or FallbackReasonCode.CAPABILITY_UNSUPPORTED,
                detail=factory_result.error,
            )

        client = factory_result.client
        payload = {
            "model": model_name,
            "instructions": request.instructions,
            "input": request.input,
            "stream": True,
        }
        previous_response_id = str(request.previous_response_id or "").strip()
        if previous_response_id:
            payload["previous_response_id"] = previous_response_id
        if request.metadata:
            payload["metadata"] = dict(request.metadata)

        try:
            stream = client.responses.create(**payload)
            aggregate = _aggregate_stream(stream, fallback_model_name=model_name)
        except ResponsesTransportError:
            raise
        except Exception as exc:
            raise ResponsesTransportError(
                reason_code=map_responses_error_to_reason_code(exc),
                detail=str(exc),
            ) from exc

        return GatewayResult(
            text=aggregate.text,
            transport_used="responses",
            provider_id=provider_id,
            model_name=aggregate.model_name or model_name,
            response_id=aggregate.response_id,
            usage=aggregate.usage,
        )


def map_responses_error_to_reason_code(error: Exception) -> FallbackReasonCode:
    status_code = _extract_status_code(error)
    if status_code in {404, 501}:
        return FallbackReasonCode.CAPABILITY_UNSUPPORTED
    if status_code == 429:
        return FallbackReasonCode.RESPONSES_RATE_LIMIT
    if status_code == 400:
        return FallbackReasonCode.RESPONSES_BAD_REQUEST
    if status_code is not None and status_code >= 500:
        return FallbackReasonCode.RESPONSES_SERVER_ERROR

    if isinstance(error, TimeoutError):
        return FallbackReasonCode.RESPONSES_TIMEOUT
    details = f"{type(error).__name__}:{error}".lower()
    if "timeout" in details:
        return FallbackReasonCode.RESPONSES_TIMEOUT
    if "429" in details or ("rate" in details and "limit" in details):
        return FallbackReasonCode.RESPONSES_RATE_LIMIT
    if "404" in details or "501" in details or "unsupported" in details:
        return FallbackReasonCode.CAPABILITY_UNSUPPORTED
    if "400" in details or "bad request" in details:
        return FallbackReasonCode.RESPONSES_BAD_REQUEST
    if any(code in details for code in ("500", "502", "503", "504", "server error")):
        return FallbackReasonCode.RESPONSES_SERVER_ERROR
    return FallbackReasonCode.RESPONSES_PARSE_ERROR


def _aggregate_stream(stream: Any, fallback_model_name: str) -> ResponsesTransportAggregate:
    if not isinstance(stream, Iterable):
        raise ResponsesTransportError(
            reason_code=FallbackReasonCode.RESPONSES_PARSE_ERROR,
            detail="responses_stream_not_iterable",
        )

    chunks: list[str] = []
    response_id = ""
    model_name = fallback_model_name
    usage: Mapping[str, Any] | None = None

    for event in stream:
        event_type = _read_field(event, "type")
        response = _read_field(event, "response")
        response_id = _read_field(event, "response_id", default=response_id) or response_id
        response_id = _read_field(response, "id", default=response_id) or response_id
        model_name = _read_field(response, "model", default=model_name) or model_name
        if usage is None:
            usage = _normalize_usage(_read_field(response, "usage"))
        if event_type == "response.output_text.delta":
            delta = _read_text_candidate(event, ("delta", "text"))
            if delta:
                chunks.append(delta)
        elif event_type == "response.output_text.done":
            done_text = _read_text_candidate(event, ("text", "delta"))
            if done_text and not chunks:
                chunks.append(done_text)
        elif event_type == "response.completed":
            completed = _read_field(event, "response", default=event)
            response_id = _read_field(completed, "id", default=response_id) or response_id
            model_name = _read_field(completed, "model", default=model_name) or model_name
            usage = _normalize_usage(_read_field(completed, "usage"))
            completed_text = _read_text_candidate(completed, ("output_text", "text"))
            if completed_text and not chunks:
                chunks.append(completed_text)

    text = "".join(chunks)
    return ResponsesTransportAggregate(
        text=text,
        response_id=response_id or None,
        model_name=model_name,
        usage=usage,
    )


def _extract_status_code(error: Exception) -> int | None:
    raw_status_code = getattr(error, "status_code", None)
    if raw_status_code is None:
        response = getattr(error, "response", None)
        raw_status_code = getattr(response, "status_code", None)
    if raw_status_code is None:
        return None
    try:
        return int(raw_status_code)
    except (TypeError, ValueError):
        return None


def _normalize_usage(value: Any) -> Mapping[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, Mapping):
        return dict(value)
    dumped = getattr(value, "to_dict", None)
    if callable(dumped):
        data = dumped()
        if isinstance(data, Mapping):
            return dict(data)
    dictionary = getattr(value, "__dict__", None)
    if isinstance(dictionary, Mapping):
        return dict(dictionary)
    return None


def _read_text_candidate(value: Any, fields: tuple[str, ...]) -> str:
    for field in fields:
        candidate = _read_field(value, field)
        if isinstance(candidate, str) and candidate:
            return candidate
    return ""


def _read_field(value: Any, field_name: str, default: Any = "") -> Any:
    if value is None:
        return default
    if isinstance(value, Mapping):
        return value.get(field_name, default)
    return getattr(value, field_name, default)
