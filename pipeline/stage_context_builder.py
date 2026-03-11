from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .contracts import (
    ContextPacket,
    ImageFacts,
    NormalizedEvent,
    ShortMemoryRecord,
    ToolIntentDecision,
    TopicAssignment,
)


class ContextBuilderStage:
    """Build context window from short-memory records and image facts."""

    def __init__(
        self,
        base_window: int = 6,
        high_frequency_window: int = 10,
        high_frequency_threshold: int = 4,
        high_frequency_minutes: int = 5,
    ) -> None:
        self.base_window = max(1, base_window)
        self.high_frequency_window = max(self.base_window, high_frequency_window)
        self.high_frequency_threshold = max(2, high_frequency_threshold)
        self.high_frequency_minutes = max(1, high_frequency_minutes)

    def build(
        self,
        event: NormalizedEvent,
        topic: TopicAssignment,
        tool_intent: ToolIntentDecision,
        image_facts: tuple[ImageFacts, ...],
        short_memory: tuple[ShortMemoryRecord, ...],
    ) -> ContextPacket:
        windowed_records = self._select_window(short_memory)

        context_lines = [
            f"[topic] id={topic.topic_id} source={topic.source}",
            f"[intent] route={tool_intent.route} confidence={tool_intent.confidence:.2f}",
        ]
        for record in windowed_records:
            context_lines.append(f"[{record.role}] {record.content}")
        for image_fact in image_facts:
            context_lines.append(f"[image] {image_fact.summary}")

        rendered_context = "\n".join(context_lines)
        return ContextPacket(
            event=event,
            topic=topic,
            tool_intent=tool_intent,
            image_facts=image_facts,
            short_memory=windowed_records,
            context_messages=tuple(record.content for record in windowed_records),
            rendered_context=rendered_context,
            metadata={
                "window_size": len(windowed_records),
                "image_fact_count": len(image_facts),
                "high_frequency_mode": len(windowed_records) > self.base_window,
            },
        )

    def _select_window(self, records: tuple[ShortMemoryRecord, ...]) -> tuple[ShortMemoryRecord, ...]:
        if len(records) <= self.base_window:
            return records

        if self._is_high_frequency(records):
            return records[-self.high_frequency_window :]
        return records[-self.base_window :]

    def _is_high_frequency(self, records: tuple[ShortMemoryRecord, ...]) -> bool:
        if len(records) < self.high_frequency_threshold:
            return False

        sample = records[-self.high_frequency_threshold :]
        parsed_times = [_parse_dt(record.created_at) for record in sample]
        if any(timestamp is None for timestamp in parsed_times):
            return False
        earliest = min(parsed_times)  # type: ignore[arg-type]
        latest = max(parsed_times)  # type: ignore[arg-type]
        return latest - earliest <= timedelta(minutes=self.high_frequency_minutes)


def _parse_dt(raw: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        try:
            parsed = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed
