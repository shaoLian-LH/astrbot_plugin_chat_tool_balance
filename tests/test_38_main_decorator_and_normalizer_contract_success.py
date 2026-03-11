from __future__ import annotations

import asyncio
import importlib
import logging
import sys
from datetime import datetime, timezone
from types import ModuleType, SimpleNamespace


class _FakeContext:
    def __init__(self, config: dict, livingmemory_client=None) -> None:
        self.config = config
        self.livingmemory_client = livingmemory_client

    def get_plugin(self, name: str):
        if "livingmemory" in name:
            return self.livingmemory_client
        return None

    async def llm_generate(self, **_kwargs):
        return "contract-llm"


class _FakeMessageEvent:
    def __init__(self) -> None:
        self.message_str = "  hello world  "
        self.sender_id = "u-contract"
        self.message_id = "msg-contract-1"
        self.conversation_type = "group"
        self.group_id = "g-contract"
        self.session_id = "session-contract"
        self.platform = "qq"
        self.created_at = datetime(2026, 3, 10, 16, 0, 0, tzinfo=timezone.utc).isoformat()
        self.image_urls = ("https://example.com/a.png",)
        self.is_bot = False
        self.unified_msg_origin = "origin-contract"

    def get_sender_id(self):
        return self.sender_id

    def get_group_id(self):
        return self.group_id

    def get_session_id(self):
        return self.session_id

    def get_conversation_type(self):
        return self.conversation_type

    def get_platform(self):
        return self.platform

    def get_extra(self, key: str, default=None):
        if key == "image_urls":
            return ["https://example.com/a.png", "https://example.com/b.png"]
        return default

    def get_messages(self):
        return [
            {"type": "image", "url": "https://example.com/c.png"},
            {"type": "text", "text": "ignored"},
        ]

    def plain_result(self, text: str):
        return text


class _StatusMessageEvent:
    def __init__(self, message_str: str) -> None:
        self.message_str = message_str
        self.sender_id = "u-contract"
        self.message_id = "msg-status-check"
        self.session_id = "session-contract"
        self.created_at = datetime(2026, 3, 10, 16, 0, 0, tzinfo=timezone.utc).isoformat()
        self.image_urls = ()
        self.is_bot = False

    def plain_result(self, text: str):
        return text


class _CaptureOrchestrator:
    def __init__(self) -> None:
        self.events = []
        self.event_contexts = []

    def handle_event(self, event, event_context=None):
        self.events.append(event)
        self.event_contexts.append(event_context)
        return SimpleNamespace(reply_text="contract-ok")


def _install_fake_astrbot_modules(monkeypatch):
    astrbot_module = ModuleType("astrbot")
    api_module = ModuleType("astrbot.api")
    event_module = ModuleType("astrbot.api.event")
    star_module = ModuleType("astrbot.api.star")

    class _Filter:
        class EventMessageType:
            ALL = "all"

        def command(self, command_name: str):
            def _decorator(fn):
                fn._fake_command_name = command_name
                return fn

            return _decorator

        def event_message_type(self, message_type):
            def _decorator(fn):
                fn._fake_event_message_type = message_type
                return fn

            return _decorator

    class AstrMessageEvent:
        def plain_result(self, text: str):
            return text

    class Context:
        pass

    class Star:
        def __init__(self, context):
            self.context = context
            self.config = None

    def register(*_args, **_kwargs):
        def _decorator(cls):
            return cls

        return _decorator

    api_module.logger = logging.getLogger("fake-astrbot")
    event_module.AstrMessageEvent = AstrMessageEvent
    event_module.filter = _Filter()
    star_module.Context = Context
    star_module.Star = Star
    star_module.register = register
    astrbot_module.api = api_module

    monkeypatch.setitem(sys.modules, "astrbot", astrbot_module)
    monkeypatch.setitem(sys.modules, "astrbot.api", api_module)
    monkeypatch.setitem(sys.modules, "astrbot.api.event", event_module)
    monkeypatch.setitem(sys.modules, "astrbot.api.star", star_module)


async def _collect_async(generator) -> list[str]:
    outputs: list[str] = []
    async for item in generator:
        outputs.append(item)
    return outputs


def test_main_decorator_binding_contract_success(monkeypatch):
    _install_fake_astrbot_modules(monkeypatch)
    sys.modules.pop("main", None)
    main_module = importlib.import_module("main")

    assert getattr(main_module.ChatToolBalancePlugin.ctb_status, "_fake_command_name", "") == "ctb_status"
    assert getattr(main_module.ChatToolBalancePlugin.on_event_message, "_fake_event_message_type", "") == "all"
    assert not hasattr(main_module, "_resolve_event_decorator")
    assert not hasattr(main_module, "_on_event_message")
    assert not hasattr(main_module, "_normalize_event")


def test_main_ctb_status_and_normalized_fields_contract_success(tmp_path, monkeypatch):
    _install_fake_astrbot_modules(monkeypatch)
    sys.modules.pop("main", None)
    main_module = importlib.import_module("main")

    context = _FakeContext(
        config={
            "models": {"chat_default": "chat-default-model"},
            "features": {"use_responses_api": False},
            "summary": {"enabled": False},
            "storage": {"base_dir": str(tmp_path / "plugin_data")},
        }
    )
    plugin = main_module.ChatToolBalancePlugin(context)

    status_before_init = asyncio.run(_collect_async(plugin.ctb_status(_StatusMessageEvent("ctb_status"))))
    assert status_before_init == ["chat_tool_balance: 未初始化"]

    asyncio.run(plugin.initialize())
    status_after_init = asyncio.run(_collect_async(plugin.ctb_status(_StatusMessageEvent("ctb_status"))))
    assert status_after_init
    assert status_after_init[0].startswith("chat_tool_balance: ready")

    capture_orchestrator = _CaptureOrchestrator()
    plugin.orchestrator = capture_orchestrator

    outputs = asyncio.run(_collect_async(plugin.on_event_message(_FakeMessageEvent())))
    assert outputs == ["contract-ok"]
    assert len(capture_orchestrator.events) == 1

    normalized_event = capture_orchestrator.events[0]
    assert normalized_event.message_id == "msg-contract-1"
    assert normalized_event.session_id == "session-contract"
    assert normalized_event.scope_id == "group:g-contract"
    assert normalized_event.user_id == "u-contract"
    assert normalized_event.text == "hello world"
    assert normalized_event.image_urls == (
        "https://example.com/a.png",
        "https://example.com/b.png",
        "https://example.com/c.png",
    )
    assert normalized_event.role == "user"
    assert normalized_event.is_bot is False
    assert normalized_event.metadata["conversation_type"] == "group"
    assert normalized_event.metadata["group_id"] == "g-contract"
    assert normalized_event.metadata["platform"] == "qq"
    assert normalized_event.metadata["unified_msg_origin"] == "origin-contract"
    assert capture_orchestrator.event_contexts[0].message_id == "msg-contract-1"

    status_message_outputs = asyncio.run(_collect_async(plugin.on_event_message(_StatusMessageEvent("/ctb_status"))))
    assert status_message_outputs == []
    assert len(capture_orchestrator.events) == 1
