from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from bridge.livingmemory_v2_bridge import LivingMemoryV2Bridge
from pipeline.contracts import NormalizedEvent, TopicAssignment
from pipeline.stage_short_memory import ShortMemoryStage
from scheduler.summary_executor import SummaryExecutor
from storage.bootstrap import initialize_storage


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

    def search_memories(self, query: str, limit: int = 5):
        return {"query": query, "limit": limit, "items": []}


def test_livingmemory_unavailable_then_retry_success(tmp_path):
    base_dir = str(tmp_path / "plugin_data")
    bootstrap = initialize_storage(base_dir=base_dir, bucket_count=10)
    path_manager = bootstrap.path_manager
    short_memory = ShortMemoryStage(path_manager=path_manager)
    topic = TopicAssignment(
        topic_id="topic-lm-retry",
        session_id="session-lm-retry",
        scope_id="scope-lm-retry",
        source="new_topic",
        confidence=0.88,
        model_name="topic-model",
        title="lm-retry-topic",
    )
    now_dt = datetime(2026, 3, 10, 12, 0, 0, tzinfo=timezone.utc)

    for idx in range(2):
        event = NormalizedEvent(
            message_id=f"lm-msg-{idx}",
            session_id=topic.session_id,
            scope_id=topic.scope_id,
            user_id="u-lm",
            text=f"content-{idx}",
            created_at=(now_dt + timedelta(minutes=idx)).isoformat(),
        )
        short_memory.append_message(event=event, topic=topic)

    with sqlite3.connect(path_manager.summary_jobs_db_path()) as conn:
        cursor = conn.execute(
            """
            INSERT INTO summary_jobs(
                scope_id, topic_id, trigger_type, dedupe_key, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, 'pending', ?, ?)
            """,
            (
                topic.scope_id,
                topic.topic_id,
                "counter_trigger",
                "counter:scope-lm-retry:topic-lm-retry:1",
                now_dt.isoformat(),
                now_dt.isoformat(),
            ),
        )
        job_id = int(cursor.lastrowid)
        conn.commit()

    holder: dict[str, object | None] = {"client": None}
    bridge = LivingMemoryV2Bridge(client_getter=lambda: holder["client"])
    executor = SummaryExecutor(
        path_manager=path_manager,
        summary_model_name="summary-model",
        bridge=bridge,
        base_retry_seconds=2,
    )

    first_result = executor.execute_job(job_id=job_id, now=now_dt)

    assert first_result is not None
    assert first_result.status == "sync_pending"
    assert first_result.pending_sync is True
    assert "plugin_not_found" in first_result.error

    with sqlite3.connect(path_manager.summary_jobs_db_path()) as conn:
        row = conn.execute(
            """
            SELECT sr.pending_sync, sr.last_sync_error, sj.status
            FROM summary_results AS sr
            JOIN summary_jobs AS sj ON sj.id = sr.job_id
            WHERE sr.job_id = ?
            """,
            (job_id,),
        ).fetchone()
    assert row is not None
    assert int(row[0]) == 1
    assert "plugin_not_found" in str(row[1])
    assert row[2] == "sync_pending"

    fake_client = _FakeLivingMemoryClient()
    holder["client"] = fake_client
    synced_count = executor.retry_pending_sync(now=now_dt + timedelta(seconds=10))
    assert synced_count == 1
    assert fake_client.add_calls == 1

    with sqlite3.connect(path_manager.summary_jobs_db_path()) as conn:
        row = conn.execute(
            """
            SELECT sr.pending_sync, sr.synced_at, sj.status, sj.error_text
            FROM summary_results AS sr
            JOIN summary_jobs AS sj ON sj.id = sr.job_id
            WHERE sr.job_id = ?
            """,
            (job_id,),
        ).fetchone()
    assert row is not None
    assert int(row[0]) == 0
    assert row[1]
    assert row[2] == "completed"
    assert row[3] is None
