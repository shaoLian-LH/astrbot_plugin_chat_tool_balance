from __future__ import annotations

import asyncio
import importlib
import logging
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from types import ModuleType


class _FakeLivingMemoryClient:
    initialized = True

    def __init__(self) -> None:
        self.add_calls = 0

    def add_memory(self, scope_id: str, topic_id: str, content: str, metadata: dict):
        self.add_calls += 1
        return {
            "scope_id": scope_id,
            "topic_id": topic_id,
            "content": content,
            "metadata": metadata,
        }


class _FakeContext:
    def __init__(self, config: dict, livingmemory_client=None) -> None:
        self.config = config
        self.livingmemory_client = livingmemory_client

    def get_plugin(self, name: str):
        if "livingmemory" in name:
            return self.livingmemory_client
        return None


class _FakeMessageEvent:
    def __init__(
        self,
        message_str: str,
        sender_id: str,
        message_id: str | None = None,
        conversation_type: str = "private",
        group_id: str | None = None,
        session_id: str | None = None,
        created_at: str | None = None,
        image_urls: tuple[str, ...] | None = None,
        is_bot: bool = False,
    ) -> None:
        self.message_str = message_str
        self.sender_id = sender_id
        self.message_id = message_id
        self.conversation_type = conversation_type
        self.group_id = group_id
        self.session_id = session_id
        self.platform = "qq"
        self.created_at = created_at or datetime.now(timezone.utc).isoformat()
        self.image_urls = tuple(image_urls or ())
        self.is_bot = is_bot
        self._extra: dict[str, object] = {}

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
        return self._extra.get(key, default)

    def get_messages(self):
        return []

    def plain_result(self, text: str):
        return text


def _install_fake_astrbot_modules(monkeypatch):
    astrbot_module = ModuleType("astrbot")
    api_module = ModuleType("astrbot.api")
    event_module = ModuleType("astrbot.api.event")
    star_module = ModuleType("astrbot.api.star")

    class _Filter:
        class EventMessageType:
            ALL = "all"

        def command(self, _name: str):
            def _decorator(fn):
                return fn

            return _decorator

        def event_message_type(self, *_args, **_kwargs):
            def _decorator(fn):
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


def _count_short_memory_messages(path_manager) -> int:
    total = 0
    for db_path in path_manager.short_memory_bucket_paths():
        with sqlite3.connect(db_path) as conn:
            row = conn.execute("SELECT COUNT(1) FROM messages").fetchone()
        if row is not None:
            total += int(row[0])
    return total


def _collect_short_memory_contents(path_manager) -> list[str]:
    contents: list[str] = []
    for db_path in path_manager.short_memory_bucket_paths():
        with sqlite3.connect(db_path) as conn:
            rows = conn.execute("SELECT content FROM messages ORDER BY id ASC").fetchall()
        for row in rows:
            if row and row[0]:
                contents.append(str(row[0]))
    return contents


def test_main_initialize_summary_wiring_and_sync_flow_success(tmp_path, monkeypatch):
    _install_fake_astrbot_modules(monkeypatch)
    sys.modules.pop("main", None)
    main_module = importlib.import_module("main")

    base_dir = str(tmp_path / "plugin_data")
    context = _FakeContext(
        config={
            "models": {
                "chat_default": "chat-default-model",
                "summary": "summary-model",
            },
            "summary": {
                "trigger_non_bot_count": 1,
                "trigger_silence_minutes": 60,
            },
            "storage": {"base_dir": base_dir},
        },
        livingmemory_client=None,
    )

    plugin = main_module.ChatToolBalancePlugin(context)
    asyncio.run(plugin.initialize())

    assert plugin.orchestrator is not None
    assert plugin.summary_executor is not None
    assert plugin.livingmemory_bridge is not None
    assert plugin.storage_bootstrap is not None

    now_dt = datetime(2026, 3, 10, 16, 0, 0, tzinfo=timezone.utc)
    event = _FakeMessageEvent(
        message_str="今天过得不错",
        sender_id="u-main-wiring",
        session_id="session-main-wiring",
        created_at=now_dt.isoformat(),
    )
    outputs = asyncio.run(_collect_async(plugin.on_event_message(event)))
    assert outputs == ["收到：今天过得不错"]

    summary_db = plugin.storage_bootstrap.path_manager.summary_jobs_db_path()
    with sqlite3.connect(summary_db) as conn:
        row = conn.execute(
            """
            SELECT sr.pending_sync, sj.status
            FROM summary_results AS sr
            JOIN summary_jobs AS sj ON sj.id = sr.job_id
            ORDER BY sr.id DESC
            LIMIT 1
            """
        ).fetchone()
    assert row is not None
    assert int(row[0]) == 1
    assert row[1] == "sync_pending"

    fake_lm_client = _FakeLivingMemoryClient()
    context.livingmemory_client = fake_lm_client
    synced_count = plugin.summary_executor.retry_pending_sync(now=now_dt + timedelta(seconds=20))
    assert synced_count == 1
    assert fake_lm_client.add_calls == 1

    with sqlite3.connect(summary_db) as conn:
        row = conn.execute(
            """
            SELECT sr.pending_sync, sj.status, sj.error_text
            FROM summary_results AS sr
            JOIN summary_jobs AS sj ON sj.id = sr.job_id
            ORDER BY sr.id DESC
            LIMIT 1
            """
        ).fetchone()
    assert row is not None
    assert int(row[0]) == 0
    assert row[1] == "completed"
    assert row[2] is None

    asyncio.run(plugin.terminate())


def test_main_image_only_message_enter_pipeline_success(tmp_path, monkeypatch):
    _install_fake_astrbot_modules(monkeypatch)
    sys.modules.pop("main", None)
    main_module = importlib.import_module("main")

    base_dir = str(tmp_path / "plugin_data")
    context = _FakeContext(
        config={
            "models": {"chat_default": "chat-default-model"},
            "summary": {"enabled": False},
            "storage": {"base_dir": base_dir},
        },
        livingmemory_client=None,
    )
    plugin = main_module.ChatToolBalancePlugin(context)
    asyncio.run(plugin.initialize())

    event = _FakeMessageEvent(
        message_str="",
        sender_id="u-image-only",
        message_id="msg-image-only-1",
        session_id="session-image-only",
        image_urls=("https://example.com/cat.png",),
    )
    outputs = asyncio.run(_collect_async(plugin.on_event_message(event)))
    assert outputs == ["已处理当前消息。"]

    assert plugin.storage_bootstrap is not None
    contents = _collect_short_memory_contents(plugin.storage_bootstrap.path_manager)
    assert len(contents) == 1
    assert "[image] image from https://example.com/cat.png" in contents[0]

    asyncio.run(plugin.terminate())


def test_main_skip_bot_event_avoid_self_loop_success(tmp_path, monkeypatch):
    _install_fake_astrbot_modules(monkeypatch)
    sys.modules.pop("main", None)
    main_module = importlib.import_module("main")

    base_dir = str(tmp_path / "plugin_data")
    context = _FakeContext(
        config={
            "models": {"chat_default": "chat-default-model"},
            "summary": {"enabled": False},
            "storage": {"base_dir": base_dir},
        },
        livingmemory_client=None,
    )
    plugin = main_module.ChatToolBalancePlugin(context)
    asyncio.run(plugin.initialize())

    event = _FakeMessageEvent(
        message_str="机器人自己的消息",
        sender_id="bot-self",
        message_id="msg-bot-self-1",
        session_id="session-bot-self",
        is_bot=True,
    )
    outputs = asyncio.run(_collect_async(plugin.on_event_message(event)))
    assert outputs == []

    assert plugin.storage_bootstrap is not None
    assert _count_short_memory_messages(plugin.storage_bootstrap.path_manager) == 0

    asyncio.run(plugin.terminate())
