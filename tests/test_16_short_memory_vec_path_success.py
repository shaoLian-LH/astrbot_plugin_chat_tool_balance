from pipeline.contracts import NormalizedEvent, TopicAssignment
from pipeline.stage_short_memory import ShortMemoryStage
from storage.bootstrap import initialize_storage


def test_short_memory_vec_unavailable_fallback_success(tmp_path):
    bootstrap = initialize_storage(base_dir=str(tmp_path / "plugin_data"), bucket_count=10)
    stage = ShortMemoryStage(
        path_manager=bootstrap.path_manager,
        vec_loader=lambda _conn: False,
    )
    topic = TopicAssignment(
        topic_id="topic-fallback",
        session_id="session-1",
        scope_id="scope-group-1",
        source="new_topic",
        confidence=0.9,
        model_name="",
        title="fallback-topic",
    )

    stage.append_message(
        event=NormalizedEvent(
            message_id="m-fallback-1",
            session_id="session-1",
            scope_id="scope-group-1",
            user_id="u1",
            text="python function design",
        ),
        topic=topic,
    )
    stage.append_message(
        event=NormalizedEvent(
            message_id="m-fallback-2",
            session_id="session-1",
            scope_id="scope-group-1",
            user_id="u1",
            text="how to cook noodles",
        ),
        topic=topic,
    )

    recalled = stage.recall_by_similarity(
        scope_id="scope-group-1",
        topic_id="topic-fallback",
        query_text="python design pattern",
        limit=1,
    )

    assert stage.vec_enabled is False
    assert stage.vec_reason.startswith("sqlite_vec_unavailable")
    assert len(recalled) == 1
    assert recalled[0].message_id == "m-fallback-1"
