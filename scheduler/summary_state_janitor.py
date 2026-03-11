from __future__ import annotations

import logging
from typing import Any

try:
    from astrbot.api import logger as astrbot_logger
except ModuleNotFoundError:  # pragma: no cover - fallback for local unit tests.
    astrbot_logger = logging.getLogger(__name__)

from ..services.llm_gateway.observability import GatewayMetricsRecorder
from ..storage.response_state_repository import ResponseStateRepository


class SummaryStateJanitor:
    """Cleanup helper for response_state records after summary completion."""

    def __init__(
        self,
        state_repository: ResponseStateRepository,
        metrics_recorder: GatewayMetricsRecorder | None = None,
        logger_obj: Any | None = None,
    ) -> None:
        self.state_repository = state_repository
        self.metrics_recorder = metrics_recorder or GatewayMetricsRecorder()
        self.logger = logger_obj or astrbot_logger

    def delete_by_scope_topic(self, scope_id: str, topic_id: str) -> int:
        deleted_count = self.state_repository.delete_by_scope_topic(
            scope_id=scope_id,
            topic_id=topic_id,
        )
        self.metrics_recorder.record_response_state_cleanup(deleted_count)
        logger_fn = getattr(self.logger, "info", None)
        if callable(logger_fn):
            payload = {
                "role": "summary",
                "provider_id": "",
                "transport_used": "",
                "fallback_reason_code": "",
                "scope_id": str(scope_id or "").strip(),
                "topic_id": str(topic_id or "").strip(),
                "latency_ms": 0.0,
                "request_id": "",
                "response_state_op": "delete",
                "deleted_count": int(deleted_count),
            }
            try:
                logger_fn("llm_gateway_observe %s", payload)
            except Exception:
                pass
        return deleted_count
