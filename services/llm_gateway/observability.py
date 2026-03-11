from __future__ import annotations

import threading
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass

from .contracts import FallbackReasonCode

RESPONSES_ATTEMPT_TOTAL = "responses_attempt_total"
RESPONSES_SUCCESS_TOTAL = "responses_success_total"
RESPONSES_FALLBACK_TOTAL = "responses_fallback_total"
RESPONSES_LATENCY_MS_BUCKET = "responses_latency_ms_bucket"
RESPONSE_STATE_HIT_TOTAL = "response_state_hit_total"
RESPONSE_STATE_CLEANUP_TOTAL = "response_state_cleanup_total"


@dataclass(frozen=True)
class MetricSample:
    name: str
    labels: tuple[tuple[str, str], ...]
    value: float


class GatewayMetricsRecorder:
    """In-memory metrics recorder for gateway observability and tests."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[tuple[str, tuple[tuple[str, str], ...]], float] = defaultdict(float)
        self._histograms: dict[tuple[str, tuple[tuple[str, str], ...]], list[float]] = defaultdict(list)

    def increment(
        self,
        name: str,
        value: float = 1,
        labels: Mapping[str, object] | None = None,
    ) -> None:
        label_key = _normalize_labels(labels)
        metric_key = (str(name), label_key)
        with self._lock:
            self._counters[metric_key] += float(value)

    def observe(
        self,
        name: str,
        value: float,
        labels: Mapping[str, object] | None = None,
    ) -> None:
        label_key = _normalize_labels(labels)
        metric_key = (str(name), label_key)
        with self._lock:
            self._histograms[metric_key].append(float(value))

    def counter_value(self, name: str, labels: Mapping[str, object] | None = None) -> float:
        label_key = _normalize_labels(labels)
        metric_key = (str(name), label_key)
        with self._lock:
            return float(self._counters.get(metric_key, 0.0))

    def histogram_values(self, name: str, labels: Mapping[str, object] | None = None) -> tuple[float, ...]:
        label_key = _normalize_labels(labels)
        metric_key = (str(name), label_key)
        with self._lock:
            return tuple(self._histograms.get(metric_key, ()))

    def snapshot(self) -> tuple[MetricSample, ...]:
        with self._lock:
            samples: list[MetricSample] = []
            for (name, labels), value in self._counters.items():
                samples.append(MetricSample(name=name, labels=labels, value=value))
            for (name, labels), values in self._histograms.items():
                samples.append(MetricSample(name=name, labels=labels, value=float(sum(values))))
        return tuple(sorted(samples, key=lambda sample: (sample.name, sample.labels)))

    def record_responses_attempt(self, role: str) -> None:
        self.increment(RESPONSES_ATTEMPT_TOTAL, labels={"role": role})

    def record_responses_success(self, role: str) -> None:
        self.increment(RESPONSES_SUCCESS_TOTAL, labels={"role": role})

    def record_responses_fallback(self, role: str, reason: FallbackReasonCode | str | None) -> None:
        reason_value = _reason_code_value(reason) or FallbackReasonCode.RESPONSES_PARSE_ERROR.value
        self.increment(
            RESPONSES_FALLBACK_TOTAL,
            labels={"role": role, "reason": reason_value},
        )

    def record_responses_latency_ms(self, role: str, latency_ms: float) -> None:
        self.observe(RESPONSES_LATENCY_MS_BUCKET, float(latency_ms), labels={"role": role})

    def record_response_state_hit(self) -> None:
        self.increment(RESPONSE_STATE_HIT_TOTAL)

    def record_response_state_cleanup(self, count: int = 1) -> None:
        self.increment(RESPONSE_STATE_CLEANUP_TOTAL, value=max(0, int(count)))


def _normalize_labels(labels: Mapping[str, object] | None) -> tuple[tuple[str, str], ...]:
    if labels is None:
        return ()
    normalized: list[tuple[str, str]] = []
    for key, value in labels.items():
        key_text = str(key).strip()
        if not key_text:
            continue
        if value is None:
            value_text = ""
        else:
            value_text = str(value)
        normalized.append((key_text, value_text))
    normalized.sort(key=lambda item: item[0])
    return tuple(normalized)


def _reason_code_value(reason: FallbackReasonCode | str | None) -> str:
    if reason is None:
        return ""
    value = getattr(reason, "value", reason)
    return str(value).strip()
