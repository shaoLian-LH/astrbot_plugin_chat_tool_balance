from __future__ import annotations

import asyncio
import inspect

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

from .handlers.event_normalizer import is_status_command_message, normalize_event
from .bridge.livingmemory_v2_bridge import LivingMemoryV2Bridge
from .pipeline.orchestrator import ChatToolBalanceOrchestrator
from .plugin_config import PluginSettings, load_plugin_settings
from .scheduler.summary_executor import SummaryExecutor
from .services.llm_gateway import LLMGateway
from .services.runtime_wiring import build_runtime_wiring
from .storage.bootstrap import StorageBootstrapResult

ORCHESTRATOR_EVENT_TIMEOUT_SECONDS = 20.0

@register("chat_tool_balance", "shaoLian-LH", "平衡聊天与工具调用插件", "v0.4.1")
class ChatToolBalancePlugin(Star):
    def __init__(self, context: Context, config=None):
        super().__init__(context)
        self.config = config
        self.settings: PluginSettings = load_plugin_settings({})
        self.storage_bootstrap: StorageBootstrapResult | None = None
        self.orchestrator: ChatToolBalanceOrchestrator | None = None
        self.llm_gateway: LLMGateway | None = None
        self.livingmemory_bridge: LivingMemoryV2Bridge | None = None
        self.summary_executor: SummaryExecutor | None = None

    async def initialize(self):
        wiring = build_runtime_wiring(self.context, self)
        self.settings = wiring.settings
        self.storage_bootstrap = wiring.storage_bootstrap
        self.llm_gateway = wiring.llm_gateway
        self.livingmemory_bridge = wiring.livingmemory_bridge
        self.summary_executor = wiring.summary_executor
        self.orchestrator = wiring.orchestrator

        lm_available, lm_reason = self.livingmemory_bridge.is_available()
        logger.info(
            "chat_tool_balance initialized, base_dir=%s, bucket_count=%s, lm_available=%s, lm_reason=%s",
            self.settings.storage.base_dir,
            self.settings.storage.bucket_count,
            lm_available,
            lm_reason,
        )

    @filter.command("ctb_status")
    async def ctb_status(self, event: AstrMessageEvent):
        if self.orchestrator is None or self.storage_bootstrap is None:
            yield event.plain_result("chat_tool_balance: 未初始化")
            return
        yield event.plain_result(
            "chat_tool_balance: ready "
            f"(base_dir={self.settings.storage.base_dir}, "
            f"bucket_count={self.settings.storage.bucket_count})"
        )

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_event_message(self, event: AstrMessageEvent):
        if self.orchestrator is None:
            logger.warning("chat_tool_balance skip event: orchestrator not initialized")
            return

        normalized_event = normalize_event(event)
        logger.info(
            "chat_tool_balance event received: message_id=%s scope_id=%s session_id=%s text_len=%s image_count=%s",
            normalized_event.message_id,
            normalized_event.scope_id,
            normalized_event.session_id,
            len(normalized_event.text.strip()),
            len(normalized_event.image_urls),
        )
        if normalized_event.is_bot:
            logger.info(
                "chat_tool_balance skip event: message_id=%s reason=bot_message",
                normalized_event.message_id,
            )
            return
        if not normalized_event.text and not normalized_event.image_urls:
            logger.info(
                "chat_tool_balance skip event: message_id=%s reason=empty_payload",
                normalized_event.message_id,
            )
            return
        if is_status_command_message(normalized_event.text):
            logger.info(
                "chat_tool_balance skip event: message_id=%s reason=status_command",
                normalized_event.message_id,
            )
            return

        try:
            reply = await asyncio.wait_for(
                asyncio.to_thread(self._run_orchestrator_sync, normalized_event, event),
                timeout=ORCHESTRATOR_EVENT_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            logger.error(
                "chat_tool_balance message handling timeout: message_id=%s timeout=%ss",
                normalized_event.message_id,
                ORCHESTRATOR_EVENT_TIMEOUT_SECONDS,
            )
            return
        except Exception as exc:
            logger.error(
                "chat_tool_balance message handling failed: message_id=%s err=%s",
                normalized_event.message_id,
                exc,
            )
            return

        logger.info(
            "chat_tool_balance event handled: message_id=%s route=%s topic_id=%s fallback=%s metadata=%s",
            normalized_event.message_id,
            getattr(reply, "route", ""),
            getattr(reply, "topic_id", ""),
            getattr(reply, "fallback_used", False),
            getattr(reply, "metadata", {}),
        )
        reply_text = (reply.reply_text or "").strip()
        if not reply_text:
            logger.info(
                "chat_tool_balance skip reply: message_id=%s reason=empty_reply_text",
                normalized_event.message_id,
            )
            return
        yield event.plain_result(reply_text)

    async def terminate(self):
        self.orchestrator = None
        self.llm_gateway = None
        self.summary_executor = None
        self.livingmemory_bridge = None

    def _run_orchestrator_sync(
        self,
        normalized_event,
        raw_event: AstrMessageEvent,
    ):
        if self.orchestrator is None:
            raise RuntimeError("orchestrator_unavailable")
        if _accepts_event_context(self.orchestrator.handle_event):
            return self.orchestrator.handle_event(normalized_event, raw_event)
        return self.orchestrator.handle_event(normalized_event)


def _accepts_event_context(callable_obj) -> bool:
    try:
        signature = inspect.signature(callable_obj)
    except (TypeError, ValueError):
        return False
    positional_params = 0
    for parameter in signature.parameters.values():
        if parameter.kind is inspect.Parameter.VAR_POSITIONAL:
            return True
        if parameter.kind in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        ):
            positional_params += 1
    return positional_params >= 2
