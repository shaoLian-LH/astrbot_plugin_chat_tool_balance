from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import replace
from typing import Any, Protocol

try:
    from astrbot.api import logger as astrbot_logger
except ModuleNotFoundError:  # pragma: no cover - fallback for local unit tests.
    astrbot_logger = logging.getLogger(__name__)

from .astrbot_transport import AstrBotTransport, AstrBotTransportRequest
from .capability_router import CapabilityRouter
from .contracts import (
    ChatSyncRequest,
    ChatSyncResult,
    FallbackReasonCode,
    GenerateSyncRequest,
    GenerateSyncResult,
)
from .observability import GatewayMetricsRecorder
from .provider_resolver import ProviderResolver
from .responses_transport import ResponsesTransport, ResponsesTransportError, ResponsesTransportRequest


class ResponseStateRepositoryProtocol(Protocol):
    def get_previous_response_id(self, scope_id: str, topic_id: str) -> str | None:
        ...

    def upsert_state(
        self,
        scope_id: str,
        topic_id: str,
        previous_response_id: str,
        provider_id: str = "",
        model_name: str = "",
        updated_at: str | None = None,
    ) -> None:
        ...


class SummaryStateJanitorProtocol(Protocol):
    def delete_by_scope_topic(self, scope_id: str, topic_id: str) -> int:
        ...


class LLMGateway:
    def __init__(
        self,
        provider_resolver: ProviderResolver,
        capability_router: CapabilityRouter,
        responses_transport: ResponsesTransport,
        astrbot_transport: AstrBotTransport,
        state_repository: ResponseStateRepositoryProtocol | None = None,
        summary_state_janitor: SummaryStateJanitorProtocol | None = None,
        metrics_recorder: GatewayMetricsRecorder | None = None,
        logger_obj: Any | None = None,
        model_name_resolver: Callable[[str, str], str] | None = None,
    ) -> None:
        self.provider_resolver = provider_resolver
        self.capability_router = capability_router
        self.responses_transport = responses_transport
        self.astrbot_transport = astrbot_transport
        self.state_repository = state_repository
        self.summary_state_janitor = summary_state_janitor
        self.metrics_recorder = metrics_recorder or GatewayMetricsRecorder()
        self.logger = logger_obj or astrbot_logger
        self.model_name_resolver = model_name_resolver or _default_model_name_resolver

    def chat_with_state_sync(self, request: ChatSyncRequest) -> ChatSyncResult:
        operation_started_at = time.perf_counter()
        role = str(request.provider_role)
        request_id = _extract_request_id(request.metadata)
        scope_id = str(request.scope_id or "").strip()
        topic_id = str(request.topic_id or "").strip()
        previous_response_id = self._read_previous_response_id(
            scope_id=scope_id,
            topic_id=topic_id,
            role=role,
            request_id=request_id,
        )
        resolved = self.provider_resolver.resolve_provider(
            provider_role=request.provider_role,
            event_context=request.event_context,
        )
        provider_id = resolved.provider_id
        model_name = self._resolve_model_name(role=role, provider_id=provider_id)
        decision = self.capability_router.decide(provider_id=provider_id)

        if decision.use_responses:
            self.metrics_recorder.record_responses_attempt(role)
            responses_started_at = time.perf_counter()
            try:
                responses_result = self.responses_transport.generate(
                    ResponsesTransportRequest(
                        provider_id=provider_id,
                        model_name=model_name,
                        instructions=request.instructions,
                        input=request.input,
                        previous_response_id=previous_response_id,
                        metadata=request.metadata,
                    )
                )
            except ResponsesTransportError as exc:
                self.metrics_recorder.record_responses_latency_ms(role, _elapsed_ms(responses_started_at))
                return self._run_fallback_path(
                    role=role,
                    provider_id=provider_id,
                    model_name=model_name,
                    instructions=request.instructions,
                    input_payload=request.input,
                    metadata=request.metadata,
                    event_context=request.event_context,
                    fallback_reason_code=exc.reason_code,
                    scope_id=scope_id,
                    topic_id=topic_id,
                    request_id=request_id,
                    operation_started_at=operation_started_at,
                )

            self.metrics_recorder.record_responses_latency_ms(role, _elapsed_ms(responses_started_at))
            self.metrics_recorder.record_responses_success(role)
            self._upsert_response_state(
                scope_id=scope_id,
                topic_id=topic_id,
                provider_id=provider_id,
                model_name=model_name,
                response_id=responses_result.response_id,
                result_provider_id=responses_result.provider_id,
                result_model_name=responses_result.model_name,
                role=role,
                request_id=request_id,
            )
            self._log_gateway_event(
                role=role,
                provider_id=responses_result.provider_id or provider_id,
                transport_used=responses_result.transport_used,
                fallback_reason_code=responses_result.fallback_reason_code,
                scope_id=scope_id,
                topic_id=topic_id,
                latency_ms=_elapsed_ms(operation_started_at),
                request_id=request_id,
            )
            return responses_result

        return self._run_fallback_path(
            role=role,
            provider_id=provider_id,
            model_name=model_name,
            instructions=request.instructions,
            input_payload=request.input,
            metadata=request.metadata,
            event_context=request.event_context,
            fallback_reason_code=decision.fallback_reason_code,
            scope_id=scope_id,
            topic_id=topic_id,
            request_id=request_id,
            operation_started_at=operation_started_at,
        )

    def generate_once_sync(self, request: GenerateSyncRequest) -> GenerateSyncResult:
        operation_started_at = time.perf_counter()
        role = str(request.provider_role)
        request_id = _extract_request_id(request.metadata)
        resolved = self.provider_resolver.resolve_provider(
            provider_role=request.provider_role,
            event_context=request.event_context,
        )
        provider_id = resolved.provider_id
        model_name = self._resolve_model_name(role=role, provider_id=provider_id)
        decision = self.capability_router.decide(provider_id=provider_id)

        if decision.use_responses:
            self.metrics_recorder.record_responses_attempt(role)
            responses_started_at = time.perf_counter()
            try:
                responses_result = self.responses_transport.generate(
                    ResponsesTransportRequest(
                        provider_id=provider_id,
                        model_name=model_name,
                        instructions=request.instructions,
                        input=request.input,
                        metadata=request.metadata,
                    )
                )
            except ResponsesTransportError as exc:
                self.metrics_recorder.record_responses_latency_ms(role, _elapsed_ms(responses_started_at))
                return self._run_fallback_path(
                    role=role,
                    provider_id=provider_id,
                    model_name=model_name,
                    instructions=request.instructions,
                    input_payload=request.input,
                    metadata=request.metadata,
                    event_context=request.event_context,
                    fallback_reason_code=exc.reason_code,
                    request_id=request_id,
                    operation_started_at=operation_started_at,
                )

            self.metrics_recorder.record_responses_latency_ms(role, _elapsed_ms(responses_started_at))
            self.metrics_recorder.record_responses_success(role)
            self._log_gateway_event(
                role=role,
                provider_id=responses_result.provider_id or provider_id,
                transport_used=responses_result.transport_used,
                fallback_reason_code=responses_result.fallback_reason_code,
                latency_ms=_elapsed_ms(operation_started_at),
                request_id=request_id,
            )
            return responses_result

        return self._run_fallback_path(
            role=role,
            provider_id=provider_id,
            model_name=model_name,
            instructions=request.instructions,
            input_payload=request.input,
            metadata=request.metadata,
            event_context=request.event_context,
            fallback_reason_code=decision.fallback_reason_code,
            request_id=request_id,
            operation_started_at=operation_started_at,
        )

    def _read_previous_response_id(
        self,
        scope_id: str,
        topic_id: str,
        role: str,
        request_id: str,
    ) -> str | None:
        if self.state_repository is None:
            self._log_gateway_event(
                role=role,
                scope_id=scope_id,
                topic_id=topic_id,
                request_id=request_id,
                response_state_op="skip",
            )
            return None

        previous_response_id = self.state_repository.get_previous_response_id(
            scope_id=scope_id,
            topic_id=topic_id,
        )
        if previous_response_id is None:
            self._log_gateway_event(
                role=role,
                scope_id=scope_id,
                topic_id=topic_id,
                request_id=request_id,
                response_state_op="read_miss",
            )
            return None
        normalized = str(previous_response_id).strip()
        if not normalized:
            self._log_gateway_event(
                role=role,
                scope_id=scope_id,
                topic_id=topic_id,
                request_id=request_id,
                response_state_op="read_miss",
            )
            return None
        self.metrics_recorder.record_response_state_hit()
        self._log_gateway_event(
            role=role,
            scope_id=scope_id,
            topic_id=topic_id,
            request_id=request_id,
            response_state_op="read_hit",
        )
        return normalized

    def _upsert_response_state(
        self,
        scope_id: str,
        topic_id: str,
        provider_id: str,
        model_name: str,
        response_id: str | None,
        result_provider_id: str = "",
        result_model_name: str = "",
        role: str = "",
        request_id: str = "",
    ) -> None:
        if self.state_repository is None:
            self._log_gateway_event(
                role=role,
                provider_id=provider_id,
                scope_id=scope_id,
                topic_id=topic_id,
                request_id=request_id,
                response_state_op="skip",
            )
            return

        response_id_value = str(response_id or "").strip()
        if not response_id_value:
            self._log_gateway_event(
                role=role,
                provider_id=provider_id,
                scope_id=scope_id,
                topic_id=topic_id,
                request_id=request_id,
                response_state_op="skip",
            )
            return
        provider_id_value = str(result_provider_id or "").strip() or provider_id
        model_name_value = str(result_model_name or "").strip() or model_name
        self.state_repository.upsert_state(
            scope_id=scope_id,
            topic_id=topic_id,
            previous_response_id=response_id_value,
            provider_id=provider_id_value,
            model_name=model_name_value,
        )
        self._log_gateway_event(
            role=role,
            provider_id=provider_id_value,
            scope_id=scope_id,
            topic_id=topic_id,
            request_id=request_id,
            response_state_op="upsert",
        )

    def _resolve_model_name(self, role: str, provider_id: str) -> str:
        try:
            resolved = self.model_name_resolver(str(role or "").strip(), str(provider_id or "").strip())
        except Exception:
            resolved = ""
        model_name = str(resolved or "").strip()
        if model_name:
            return model_name
        return str(provider_id or "").strip()

    def _run_fallback_path(
        self,
        role: str,
        provider_id: str,
        model_name: str,
        instructions: str,
        input_payload: object,
        metadata: Mapping[str, object],
        event_context: object,
        fallback_reason_code: FallbackReasonCode | None,
        operation_started_at: float,
        scope_id: str = "",
        topic_id: str = "",
        request_id: str = "",
    ) -> GenerateSyncResult:
        try:
            fallback_result = self._generate_fallback_result(
                provider_id=provider_id,
                model_name=model_name,
                instructions=instructions,
                input_payload=input_payload,
                metadata=metadata,
                event_context=event_context,
                fallback_reason_code=fallback_reason_code,
            )
        except Exception as exc:
            reason_code = _reason_code_from_exception(exc, fallback_reason_code)
            self.metrics_recorder.record_responses_fallback(role, reason_code)
            self._log_gateway_event(
                role=role,
                provider_id=provider_id,
                transport_used="fallback_chat",
                fallback_reason_code=reason_code,
                scope_id=scope_id,
                topic_id=topic_id,
                latency_ms=_elapsed_ms(operation_started_at),
                request_id=request_id,
                error=str(exc),
            )
            raise

        reason_code = fallback_result.fallback_reason_code or fallback_reason_code
        self.metrics_recorder.record_responses_fallback(role, reason_code)
        self._log_gateway_event(
            role=role,
            provider_id=fallback_result.provider_id or provider_id,
            transport_used=fallback_result.transport_used,
            fallback_reason_code=reason_code,
            scope_id=scope_id,
            topic_id=topic_id,
            latency_ms=_elapsed_ms(operation_started_at),
            request_id=request_id,
        )
        return fallback_result

    def _generate_fallback_result(
        self,
        provider_id: str,
        model_name: str,
        instructions: str,
        input_payload: object,
        metadata: Mapping[str, object],
        event_context: object,
        fallback_reason_code: FallbackReasonCode | None,
    ) -> GenerateSyncResult:
        fallback_result = self.astrbot_transport.generate(
            AstrBotTransportRequest(
                provider_id=provider_id,
                model_name=model_name,
                instructions=instructions,
                input=input_payload,
                metadata=dict(metadata),
                event_context=event_context,
            )
        )
        if fallback_reason_code is None:
            return fallback_result
        if fallback_result.fallback_reason_code is not None:
            return fallback_result
        return replace(fallback_result, fallback_reason_code=fallback_reason_code)

    def _log_gateway_event(
        self,
        role: str,
        provider_id: str = "",
        transport_used: str = "",
        fallback_reason_code: FallbackReasonCode | str | None = None,
        scope_id: str = "",
        topic_id: str = "",
        latency_ms: float | None = None,
        request_id: str = "",
        response_state_op: str = "",
        error: str = "",
    ) -> None:
        payload: dict[str, Any] = {
            "role": str(role or "").strip(),
            "provider_id": str(provider_id or "").strip(),
            "transport_used": str(transport_used or "").strip(),
            "fallback_reason_code": _reason_code_value(fallback_reason_code),
            "scope_id": str(scope_id or "").strip(),
            "topic_id": str(topic_id or "").strip(),
            "latency_ms": 0.0 if latency_ms is None else round(float(latency_ms), 3),
            "request_id": str(request_id or "").strip(),
        }
        if response_state_op:
            payload["response_state_op"] = str(response_state_op)
        if error:
            payload["error"] = str(error)

        logger_fn = getattr(self.logger, "info", None)
        if not callable(logger_fn):
            return
        try:
            logger_fn("llm_gateway_observe %s", payload)
        except Exception:
            return


def _extract_request_id(metadata: Mapping[str, object]) -> str:
    for key in ("request_id", "trace_id", "message_id", "job_id", "session_id"):
        value = metadata.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def _reason_code_value(reason: FallbackReasonCode | str | None) -> str:
    if reason is None:
        return ""
    value = getattr(reason, "value", reason)
    return str(value).strip()


def _reason_code_from_exception(
    error: Exception,
    fallback_reason_code: FallbackReasonCode | None,
) -> FallbackReasonCode | str:
    reason = getattr(error, "reason_code", None)
    if reason is not None:
        return reason
    if fallback_reason_code is not None:
        return fallback_reason_code
    return FallbackReasonCode.FALLBACK_FAILED


def _elapsed_ms(started_at: float) -> float:
    return max(0.0, (time.perf_counter() - float(started_at)) * 1000.0)


def _default_model_name_resolver(_role: str, provider_id: str) -> str:
    return str(provider_id or "").strip()
