from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from pipeline.contracts import (
    ContextPacket,
    ImageFacts,
    NormalizedEvent,
    OrchestratorReply,
    ToolIntentDecision,
)
from pipeline.stage_context_builder import ContextBuilderStage
from pipeline.stage_image_ocr import ImageOCRStage
from pipeline.stage_short_memory import ShortMemoryStage
from pipeline.stage_tool_intent import ToolIntentStage
from pipeline.stage_topic_router import TopicRouterStage
from plugin_config import PluginSettings
from scheduler.summary_executor import SummaryExecutor
from scheduler.summary_scheduler import SummaryJobRecord, SummaryScheduler
from storage.path_manager import StoragePathManager

ToolExecutor = Callable[[NormalizedEvent, str], str]
ChatResponder = Callable[[ContextPacket], str]


@dataclass
class ChatToolBalanceOrchestrator:
    """Orchestrate tool-first routing with chat fallback and summary hooks."""

    settings: PluginSettings
    path_manager: StoragePathManager | None = None
    tool_executor: ToolExecutor | None = None
    chat_responder: ChatResponder | None = None
    summary_scheduler: SummaryScheduler | None = None
    summary_executor: SummaryExecutor | None = None

    def __post_init__(self) -> None:
        if self.path_manager is None:
            self.path_manager = StoragePathManager(
                base_dir=self.settings.storage.base_dir,
                bucket_count=self.settings.storage.bucket_count,
            )

        self.image_stage = ImageOCRStage(path_manager=self.path_manager)
        self.tool_intent_stage = ToolIntentStage(
            tool_intent_model=self.settings.models.tool_intent_classifier,
            chat_default_model=self.settings.models.chat_default,
        )
        self.topic_router_stage = TopicRouterStage(
            path_manager=self.path_manager,
            topic_model_name=self.settings.models.topic_classifier,
            chat_default_model=self.settings.models.chat_default,
        )
        self.short_memory_stage = ShortMemoryStage(path_manager=self.path_manager)
        self.context_builder_stage = ContextBuilderStage()
        self.summary_enabled = bool(self.settings.summary.enabled)
        if self.tool_executor is None:
            self.tool_executor = _default_tool_executor
        if self.chat_responder is None:
            self.chat_responder = _default_chat_responder
        if self.summary_scheduler is None:
            self.summary_scheduler = SummaryScheduler(
                path_manager=self.path_manager,
                trigger_non_bot_count=self.settings.summary.trigger.trigger_non_bot_count,
                trigger_silence_minutes=self.settings.summary.trigger.trigger_silence_minutes,
            )

    def run_pre_reply_pipeline(self, event: NormalizedEvent) -> ContextPacket:
        """Kept for compatibility with phase-2 tests and local diagnostics."""
        image_facts = self.image_stage.process(event)
        tool_intent = self.tool_intent_stage.process(event, image_facts=image_facts)
        return self._build_chat_context(
            event=event,
            image_facts=image_facts,
            tool_intent=tool_intent,
        )

    def handle_event(self, event: NormalizedEvent) -> OrchestratorReply:
        image_facts = self.image_stage.process(event)
        tool_intent = self.tool_intent_stage.process(event, image_facts=image_facts)
        tool_reply = ""
        tool_error = ""

        if tool_intent.hit:
            tool_reply, tool_error = self._try_tool_first(event=event, tool_intent=tool_intent)

        context_packet = self._build_chat_context(
            event=event,
            image_facts=image_facts,
            tool_intent=tool_intent,
        )
        summary_jobs: tuple[SummaryJobRecord, ...] = ()
        executed_jobs = 0
        synced_jobs = 0
        if self.summary_enabled:
            summary_jobs = self._schedule_summary_jobs(event=event, context_packet=context_packet)
            executed_jobs = self._execute_summary_jobs(summary_jobs)
            synced_jobs = (
                self.summary_executor.retry_pending_sync(limit=10)
                if self.summary_executor is not None
                else 0
            )
        if tool_intent.hit and tool_reply:
            return OrchestratorReply(
                message_id=event.message_id,
                route="tool",
                reply_text=tool_reply,
                topic_id=context_packet.topic.topic_id,
                tool_used=True,
                fallback_used=False,
                metadata={
                    "tool_intent_confidence": tool_intent.confidence,
                    "tool_reason_code": tool_intent.reason_code,
                    "summary_job_count": len(summary_jobs),
                    "summary_executed_count": executed_jobs,
                    "summary_sync_retry_success_count": synced_jobs,
                },
            )

        reply_text = self.chat_responder(context_packet)
        return OrchestratorReply(
            message_id=event.message_id,
            route="chat",
            reply_text=reply_text,
            topic_id=context_packet.topic.topic_id,
            tool_used=False,
            fallback_used=tool_intent.hit,
            metadata={
                "tool_fallback_reason": tool_error,
                "summary_job_count": len(summary_jobs),
                "summary_executed_count": executed_jobs,
                "summary_sync_retry_success_count": synced_jobs,
            },
        )

    def _build_chat_context(
        self,
        event: NormalizedEvent,
        image_facts: tuple[ImageFacts, ...],
        tool_intent: ToolIntentDecision,
    ) -> ContextPacket:
        topic_assignment = self.topic_router_stage.assign_topic(event)
        self.short_memory_stage.append_message(
            event=event,
            topic=topic_assignment,
            image_facts=image_facts,
        )
        short_memory = self.short_memory_stage.recall_recent(
            scope_id=event.scope_id,
            topic_id=topic_assignment.topic_id,
        )
        return self.context_builder_stage.build(
            event=event,
            topic=topic_assignment,
            tool_intent=tool_intent,
            image_facts=image_facts,
            short_memory=short_memory,
        )

    def _try_tool_first(
        self,
        event: NormalizedEvent,
        tool_intent: ToolIntentDecision,
    ) -> tuple[str, str]:
        assert self.tool_executor is not None
        try:
            reply = (self.tool_executor(event, tool_intent.prompt_injection) or "").strip()
        except Exception as exc:
            return "", f"tool_exec_error:{exc}"
        if not reply:
            return "", "tool_empty_result"
        return reply, ""

    def _schedule_summary_jobs(
        self,
        event: NormalizedEvent,
        context_packet: ContextPacket,
    ) -> tuple[SummaryJobRecord, ...]:
        if self.summary_scheduler is None:
            return ()
        counter_jobs = self.summary_scheduler.record_topic_activity(
            event=event,
            topic=context_packet.topic,
        )
        silence_jobs = self.summary_scheduler.poll_silence()
        return counter_jobs + silence_jobs

    def _execute_summary_jobs(self, jobs: tuple[SummaryJobRecord, ...]) -> int:
        if self.summary_executor is None:
            return 0
        executed = 0
        for job in jobs:
            result = self.summary_executor.execute_job(job.id)
            if result is not None:
                executed += 1
        return executed


def _default_tool_executor(_event: NormalizedEvent, _prompt: str) -> str:
    return ""


def _default_chat_responder(context_packet: ContextPacket) -> str:
    text = context_packet.event.text.strip()
    if text:
        return f"收到：{text}"
    return "已处理当前消息。"
