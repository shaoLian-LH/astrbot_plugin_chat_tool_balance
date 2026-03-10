from __future__ import annotations

from datetime import datetime, timedelta, timezone

from pipeline.contracts import NormalizedEvent, TopicAssignment
from scheduler.summary_scheduler import SummaryScheduler
from storage.bootstrap import initialize_storage


def test_summary_trigger_counter_and_silence_dedup_success(tmp_path):
    base_dir = str(tmp_path / "plugin_data")
    bootstrap = initialize_storage(base_dir=base_dir, bucket_count=10)
    scheduler = SummaryScheduler(
        path_manager=bootstrap.path_manager,
        trigger_non_bot_count=20,
        trigger_silence_minutes=60,
    )
    topic = TopicAssignment(
        topic_id="topic-phase3",
        session_id="session-phase3",
        scope_id="scope-phase3",
        source="new_topic",
        confidence=0.99,
        model_name="topic-model",
        title="phase3-topic",
    )
    base_time = datetime(2026, 3, 10, 0, 0, 0, tzinfo=timezone.utc)

    counter_jobs = ()
    for idx in range(20):
        event_time = base_time + timedelta(minutes=idx)
        event = NormalizedEvent(
            message_id=f"m-counter-{idx}",
            session_id=topic.session_id,
            scope_id=topic.scope_id,
            user_id="u-counter",
            text=f"message-{idx}",
            created_at=event_time.isoformat(),
        )
        jobs = scheduler.record_topic_activity(event=event, topic=topic, now=event_time)
        if idx < 19:
            assert jobs == ()
        else:
            counter_jobs = jobs

    assert len(counter_jobs) == 1
    assert counter_jobs[0].trigger_type == "counter_trigger"
    assert counter_jobs[0].dedupe_key == "counter:scope-phase3:topic-phase3:1"

    silence_now = base_time + timedelta(minutes=81)
    silence_jobs = scheduler.poll_silence(now=silence_now)
    assert len(silence_jobs) == 1
    assert silence_jobs[0].trigger_type == "silence_trigger"
    assert silence_jobs[0].dedupe_key

    silence_jobs_again = scheduler.poll_silence(now=silence_now + timedelta(minutes=1))
    assert silence_jobs_again == ()
