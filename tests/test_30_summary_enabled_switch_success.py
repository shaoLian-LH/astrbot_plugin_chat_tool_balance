from __future__ import annotations

import sqlite3

from bridge.livingmemory_v2_bridge import LivingMemoryV2Bridge
from pipeline.contracts import NormalizedEvent
from pipeline.orchestrator import ChatToolBalanceOrchestrator
from plugin_config import load_plugin_settings
from scheduler.summary_executor import SummaryExecutor
from storage.bootstrap import initialize_storage


def test_summary_enabled_false_skip_scheduler_and_executor_success(tmp_path):
    base_dir = str(tmp_path / "plugin_data")
    bootstrap = initialize_storage(base_dir=base_dir, bucket_count=10)
    settings = load_plugin_settings(
        {
            "models": {
                "chat_default": "chat-model",
                "tool_intent_classifier": "tool-model",
                "topic_classifier": "topic-model",
                "summary": "summary-model",
            },
            "summary": {
                "enabled": False,
                "trigger_non_bot_count": 1,
                "trigger_silence_minutes": 1,
            },
            "storage": {"base_dir": base_dir},
        }
    )
    executor = SummaryExecutor(
        path_manager=bootstrap.path_manager,
        summary_model_name="summary-model",
        bridge=LivingMemoryV2Bridge(client_getter=lambda: None),
    )
    orchestrator = ChatToolBalanceOrchestrator(
        settings=settings,
        path_manager=bootstrap.path_manager,
        summary_executor=executor,
    )
    event = NormalizedEvent(
        message_id="m-summary-disabled-1",
        session_id="session-summary-disabled",
        scope_id="scope-summary-disabled",
        user_id="u-summary-disabled",
        text="今天状态很好",
    )

    reply = orchestrator.handle_event(event)

    assert reply.route == "chat"
    assert reply.topic_id
    assert int(reply.metadata.get("summary_job_count", 0)) == 0
    assert int(reply.metadata.get("summary_executed_count", 0)) == 0
    assert int(reply.metadata.get("summary_sync_retry_success_count", 0)) == 0

    with sqlite3.connect(bootstrap.path_manager.summary_jobs_db_path()) as conn:
        job_count = conn.execute("SELECT COUNT(1) FROM summary_jobs").fetchone()
        result_count = conn.execute("SELECT COUNT(1) FROM summary_results").fetchone()
    assert job_count is not None
    assert result_count is not None
    assert int(job_count[0]) == 0
    assert int(result_count[0]) == 0
