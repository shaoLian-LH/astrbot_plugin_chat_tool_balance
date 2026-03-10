from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from bridge.livingmemory_v2_bridge import LivingMemoryV2Bridge
from pipeline.contracts import NormalizedEvent, TopicAssignment
from pipeline.stage_short_memory import ShortMemoryStage
from scheduler.summary_executor import SummaryExecutor
from storage.bootstrap import initialize_storage


class _StableLivingMemoryClient:
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


def test_summary_executor_retry_failed_job_success(tmp_path):
    base_dir = str(tmp_path / "plugin_data")
    bootstrap = initialize_storage(base_dir=base_dir, bucket_count=10)
    path_manager = bootstrap.path_manager
    short_memory = ShortMemoryStage(path_manager=path_manager)
    topic = TopicAssignment(
        topic_id="topic-retry-failed",
        session_id="session-retry-failed",
        scope_id="scope-retry-failed",
        source="new_topic",
        confidence=0.9,
        model_name="topic-model",
        title="retry-failed-topic",
    )
    base_now = datetime(2026, 3, 10, 10, 0, 0, tzinfo=timezone.utc)

    short_memory.append_message(
        event=NormalizedEvent(
            message_id="retry-failed-msg-1",
            session_id=topic.session_id,
            scope_id=topic.scope_id,
            user_id="u-retry",
            text="message for retry failed test",
            created_at=base_now.isoformat(),
        ),
        topic=topic,
    )

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
                "counter:scope-retry-failed:topic-retry-failed:1",
                base_now.isoformat(),
                base_now.isoformat(),
            ),
        )
        job_id = int(cursor.lastrowid)
        conn.commit()

    call_counter = {"value": 0}

    def flaky_summary_generator(records, _model_name):
        call_counter["value"] += 1
        if call_counter["value"] == 1:
            raise RuntimeError("summary_model_timeout")
        assert records
        return "summary-after-retry", 0.88

    client = _StableLivingMemoryClient()
    executor = SummaryExecutor(
        path_manager=path_manager,
        summary_model_name="summary-model",
        summary_generator=flaky_summary_generator,
        bridge=LivingMemoryV2Bridge(client_getter=lambda: client),
        base_retry_seconds=1,
    )

    first = executor.execute_job(job_id=job_id, now=base_now)
    assert first is not None
    assert first.status == "failed"
    assert first.pending_sync is False
    assert "summary_model_timeout" in first.error

    with sqlite3.connect(path_manager.summary_jobs_db_path()) as conn:
        row = conn.execute(
            """
            SELECT status, retry_count, next_retry_at, error_text
            FROM summary_jobs
            WHERE id = ?
            """,
            (job_id,),
        ).fetchone()
    assert row is not None
    assert row[0] == "failed"
    assert int(row[1]) == 1
    assert row[2]
    assert "summary_model_timeout" in str(row[3])

    retried = executor.retry_failed_jobs(now=base_now + timedelta(seconds=2), limit=5)
    assert len(retried) == 1
    assert retried[0].status == "completed"
    assert retried[0].pending_sync is False
    assert call_counter["value"] == 2
    assert client.add_calls == 1

    with sqlite3.connect(path_manager.summary_jobs_db_path()) as conn:
        row = conn.execute(
            """
            SELECT sj.status, sj.error_text, sr.pending_sync, sr.summary_text
            FROM summary_jobs AS sj
            JOIN summary_results AS sr ON sr.job_id = sj.id
            WHERE sj.id = ?
            """,
            (job_id,),
        ).fetchone()
    assert row is not None
    assert row[0] == "completed"
    assert row[1] is None
    assert int(row[2]) == 0
    assert row[3] == "summary-after-retry"
