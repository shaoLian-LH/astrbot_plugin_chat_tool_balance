from __future__ import annotations

from astrbot_plugin_chat_tool_balance.pipeline.contracts import NormalizedEvent
from astrbot_plugin_chat_tool_balance.pipeline.orchestrator import ChatToolBalanceOrchestrator
from astrbot_plugin_chat_tool_balance.plugin_config import load_plugin_settings
from astrbot_plugin_chat_tool_balance.storage.bootstrap import initialize_storage


def test_orchestrator_tool_first_reply_success(tmp_path):
    base_dir = str(tmp_path / "plugin_data")
    initialize_storage(base_dir=base_dir, bucket_count=10)
    settings = load_plugin_settings(
        {
            "models": {
                "chat_default": "chat-model",
                "tool_intent_classifier": "tool-model",
                "topic_classifier": "topic-model",
            },
            "storage": {"base_dir": base_dir},
        }
    )

    tool_calls: list[tuple[str, str]] = []
    chat_calls: list[str] = []

    def tool_executor(event: NormalizedEvent, prompt: str) -> str:
        tool_calls.append((event.message_id, prompt))
        return "tool-weather-result"

    def chat_responder(context_packet) -> str:
        chat_calls.append(context_packet.event.message_id)
        return "chat-fallback"

    orchestrator = ChatToolBalanceOrchestrator(
        settings=settings,
        tool_executor=tool_executor,
        chat_responder=chat_responder,
    )
    event = NormalizedEvent(
        message_id="m-tool-1",
        session_id="group-session-1",
        scope_id="group-100",
        user_id="u-1",
        text="请帮我查询明天上海天气",
    )

    reply = orchestrator.handle_event(event)

    assert reply.route == "tool"
    assert reply.reply_text == "tool-weather-result"
    assert reply.tool_used is True
    assert reply.fallback_used is False
    assert reply.topic_id
    assert len(tool_calls) == 1
    assert tool_calls[0][0] == "m-tool-1"
    assert tool_calls[0][1]
    assert chat_calls == []
    records = orchestrator.short_memory_stage.recall_recent(
        scope_id=event.scope_id,
        topic_id=reply.topic_id,
    )
    assert any(item.message_id == event.message_id for item in records)


def test_orchestrator_tool_error_fallback_chat_success(tmp_path):
    base_dir = str(tmp_path / "plugin_data")
    initialize_storage(base_dir=base_dir, bucket_count=10)
    settings = load_plugin_settings(
        {
            "models": {
                "chat_default": "chat-model",
                "tool_intent_classifier": "tool-model",
                "topic_classifier": "topic-model",
            },
            "storage": {"base_dir": base_dir},
        }
    )

    chat_calls: list[str] = []

    def tool_executor(_event: NormalizedEvent, _prompt: str) -> str:
        raise RuntimeError("tool_down")

    def chat_responder(context_packet) -> str:
        chat_calls.append(context_packet.event.message_id)
        return "chat-fallback-success"

    orchestrator = ChatToolBalanceOrchestrator(
        settings=settings,
        tool_executor=tool_executor,
        chat_responder=chat_responder,
    )
    event = NormalizedEvent(
        message_id="m-tool-2",
        session_id="private-session-1",
        scope_id="private-u-2",
        user_id="u-2",
        text="run weather tool now",
    )

    reply = orchestrator.handle_event(event)

    assert reply.route == "chat"
    assert reply.reply_text == "chat-fallback-success"
    assert reply.tool_used is False
    assert reply.fallback_used is True
    assert reply.topic_id
    assert chat_calls == ["m-tool-2"]
    assert str(reply.metadata.get("tool_fallback_reason", "")).startswith("tool_exec_error:")
