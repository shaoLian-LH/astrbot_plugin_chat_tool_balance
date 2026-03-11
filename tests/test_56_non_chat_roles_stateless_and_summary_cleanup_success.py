from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from types import SimpleNamespace

from pipeline.contracts import NormalizedEvent, TopicAssignment
from services.runtime_wiring import build_runtime_wiring


class _StableLivingMemoryClient:
    initialized = True

    def add_memory(self, scope_id: str, topic_id: str, content: str, metadata: dict):
        return {
            "scope_id": scope_id,
            "topic_id": topic_id,
            "content": content,
            "metadata": metadata,
        }


class _Phase7RuntimeContext:
    def __init__(self, config: dict, livingmemory_client: _StableLivingMemoryClient | None) -> None:
        self.config = config
        self.livingmemory_client = livingmemory_client
        self.llm_calls: list[dict[str, object]] = []

    def get_plugin(self, name: str):
        if "livingmemory" in name:
            return self.livingmemory_client
        return None

    async def llm_generate(self, **kwargs):
        self.llm_calls.append(dict(kwargs))
        prompt = str(kwargs.get("prompt", ""))
        if "OCR 图像描述助手" in prompt:
            return "gateway-ocr-description"
        if "工具意图分类器" in prompt:
            return '{"route":"tool","confidence":0.92,"reason":"gateway_tool"}'
        if "主题路由分类器" in prompt:
            return '{"topic_id":"topic_phase7_gateway","confidence":0.93}'
        if "对话总结助手" in prompt:
            return '{"summary":"gateway-summary-text","quality":0.9}'
        return "fallback-text"


def test_non_chat_roles_stateless_and_summary_cleanup_success(tmp_path):
    base_dir = str(tmp_path / "plugin_data")
    scope_id = "scope-phase7"
    topic_completed = "topic_phase7_gateway"
    topic_failed = "topic_phase7_failed"
    topic_sync_pending = "topic_phase7_sync_pending"
    session_id = "session-phase7"
    now_iso = datetime(2026, 3, 11, 10, 0, 0, tzinfo=timezone.utc).isoformat()

    config = {
        "models": {
            "chat_default": "provider-chat",
            "ocr": "provider-ocr",
            "tool_intent_classifier": "provider-tool-intent",
            "topic_classifier": "provider-topic",
            "summary": "provider-summary",
        },
        "features": {"use_responses_api": False},
        "summary": {"enabled": True},
        "storage": {"base_dir": base_dir},
    }
    context = _Phase7RuntimeContext(config=config, livingmemory_client=_StableLivingMemoryClient())
    plugin = SimpleNamespace(config=config)
    wiring = build_runtime_wiring(context=context, plugin=plugin)
    state_repository = wiring.llm_gateway.state_repository
    assert state_repository is not None

    state_repository.upsert_state(
        scope_id=scope_id,
        topic_id=topic_completed,
        previous_response_id="resp_state_seed_completed",
        provider_id="provider-chat",
        model_name="provider-chat",
        updated_at=now_iso,
    )
    seeded_completed = state_repository.get_state(scope_id=scope_id, topic_id=topic_completed)
    assert seeded_completed is not None

    event = NormalizedEvent(
        message_id="msg-phase7-non-chat",
        session_id=session_id,
        scope_id=scope_id,
        user_id="user-phase7",
        text="请帮我查一下今天杭州天气，并看看这张图",
        image_urls=("https://example.com/cat.png",),
        metadata={"unified_msg_origin": "origin-phase7"},
    )

    image_facts = wiring.orchestrator.image_stage.process(event)
    assert len(image_facts) == 1
    assert image_facts[0].description == "gateway-ocr-description"
    assert image_facts[0].metadata["provider"] == "llm_gateway"

    tool_intent = wiring.orchestrator.tool_intent_stage.process(event, image_facts=image_facts)
    assert tool_intent.route == "tool"
    assert tool_intent.reason_code == "gateway_tool"

    topic_assignment = wiring.orchestrator.topic_router_stage.assign_topic(event)
    assert topic_assignment.topic_id == topic_completed
    assert topic_assignment.source == "model_classify"

    state_after_non_chat = state_repository.get_state(scope_id=scope_id, topic_id=topic_completed)
    assert state_after_non_chat is not None
    assert state_after_non_chat.previous_response_id == "resp_state_seed_completed"

    completed_result = _execute_summary_job(
        wiring=wiring,
        scope_id=scope_id,
        topic=TopicAssignment(
            topic_id=topic_completed,
            session_id=session_id,
            scope_id=scope_id,
            source="model_classify",
            confidence=0.93,
            model_name="provider-topic",
            title="phase7-completed-topic",
        ),
        message_id="msg-phase7-summary-completed",
        dedupe_key="phase7-completed",
        now_iso=now_iso,
    )
    assert completed_result is not None
    assert completed_result.status == "completed"
    assert completed_result.pending_sync is False
    assert state_repository.get_state(scope_id=scope_id, topic_id=topic_completed) is None

    state_repository.upsert_state(
        scope_id=scope_id,
        topic_id=topic_failed,
        previous_response_id="resp_state_seed_failed",
        provider_id="provider-chat",
        model_name="provider-chat",
        updated_at=now_iso,
    )
    original_summary_generator = wiring.summary_executor.summary_generator
    wiring.summary_executor.summary_generator = _failing_summary_generator
    failed_result = _execute_summary_job(
        wiring=wiring,
        scope_id=scope_id,
        topic=TopicAssignment(
            topic_id=topic_failed,
            session_id=session_id,
            scope_id=scope_id,
            source="new_topic",
            confidence=0.7,
            model_name="provider-topic",
            title="phase7-failed-topic",
        ),
        message_id="msg-phase7-summary-failed",
        dedupe_key="phase7-failed",
        now_iso=now_iso,
    )
    assert failed_result is not None
    assert failed_result.status == "failed"
    assert state_repository.get_state(scope_id=scope_id, topic_id=topic_failed) is not None

    state_repository.upsert_state(
        scope_id=scope_id,
        topic_id=topic_sync_pending,
        previous_response_id="resp_state_seed_pending",
        provider_id="provider-chat",
        model_name="provider-chat",
        updated_at=now_iso,
    )
    wiring.summary_executor.summary_generator = original_summary_generator
    original_bridge = wiring.summary_executor.bridge
    wiring.summary_executor.bridge = None
    sync_pending_result = _execute_summary_job(
        wiring=wiring,
        scope_id=scope_id,
        topic=TopicAssignment(
            topic_id=topic_sync_pending,
            session_id=session_id,
            scope_id=scope_id,
            source="new_topic",
            confidence=0.7,
            model_name="provider-topic",
            title="phase7-sync-pending-topic",
        ),
        message_id="msg-phase7-summary-sync-pending",
        dedupe_key="phase7-sync-pending",
        now_iso=now_iso,
    )
    assert sync_pending_result is not None
    assert sync_pending_result.status == "sync_pending"
    assert sync_pending_result.pending_sync is True
    assert state_repository.get_state(scope_id=scope_id, topic_id=topic_sync_pending) is not None
    wiring.summary_executor.bridge = original_bridge


def _execute_summary_job(
    wiring,
    scope_id: str,
    topic: TopicAssignment,
    message_id: str,
    dedupe_key: str,
    now_iso: str,
):
    event = NormalizedEvent(
        message_id=message_id,
        session_id=topic.session_id,
        scope_id=scope_id,
        user_id="summary-user",
        text=f"summary-source-{topic.topic_id}",
        created_at=now_iso,
    )
    wiring.orchestrator.short_memory_stage.append_message(
        event=event,
        topic=topic,
    )
    with sqlite3.connect(wiring.storage_bootstrap.path_manager.summary_jobs_db_path()) as conn:
        cursor = conn.execute(
            """
            INSERT INTO summary_jobs(
                scope_id, topic_id, trigger_type, dedupe_key, status, created_at, updated_at
            ) VALUES (?, ?, 'counter_trigger', ?, 'pending', ?, ?)
            """,
            (scope_id, topic.topic_id, dedupe_key, now_iso, now_iso),
        )
        job_id = int(cursor.lastrowid)
        conn.commit()
    return wiring.summary_executor.execute_job(job_id=job_id, now=datetime.fromisoformat(now_iso))


def _failing_summary_generator(_records, _model_name):
    raise RuntimeError("summary_model_failed_for_test")
