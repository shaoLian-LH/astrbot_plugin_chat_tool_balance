import sqlite3

from pipeline.contracts import NormalizedEvent, TopicAssignment
from pipeline.stage_short_memory import ShortMemoryStage
from pipeline.stage_topic_router import TopicRouterStage
from storage.bootstrap import initialize_storage


def test_topic_router_rule_fallback_success(tmp_path):
    bootstrap = initialize_storage(base_dir=str(tmp_path / "plugin_data"), bucket_count=10)
    with sqlite3.connect(bootstrap.path_manager.core_db_path()) as conn:
        conn.execute(
            "INSERT INTO topics(topic_id, scope_id, title) VALUES (?, ?, ?)",
            ("weather-topic", "scope-group-1", "weather"),
        )
        conn.commit()

    def failing_classifier(_event: NormalizedEvent, _model_name: str):
        raise RuntimeError("model offline")

    stage = TopicRouterStage(
        path_manager=bootstrap.path_manager,
        topic_model_name="topic-model",
        chat_default_model="chat-default",
        classifier=failing_classifier,
    )
    event = NormalizedEvent(
        message_id="m-topic-01",
        session_id="session-1",
        scope_id="scope-group-1",
        user_id="user-a",
        text="weather in shanghai tonight",
    )

    assignment = stage.assign_topic(event)

    assert assignment.topic_id == "weather-topic"
    assert assignment.source == "rule_match"


def test_topic_router_vec_fallback_success(tmp_path):
    bootstrap = initialize_storage(base_dir=str(tmp_path / "plugin_data"), bucket_count=10)

    with sqlite3.connect(bootstrap.path_manager.core_db_path()) as conn:
        conn.execute(
            "INSERT INTO topics(topic_id, scope_id, title) VALUES (?, ?, ?)",
            ("topic_python", "scope-group-1", "finance"),
        )
        conn.commit()

    short_memory_stage = ShortMemoryStage(path_manager=bootstrap.path_manager)
    seed_topic = TopicAssignment(
        topic_id="topic_python",
        session_id="session-1",
        scope_id="scope-group-1",
        source="new_topic",
        confidence=0.9,
        model_name="",
        title="finance",
    )
    short_memory_stage.append_message(
        event=NormalizedEvent(
            message_id="m-topic-seed",
            session_id="session-1",
            scope_id="scope-group-1",
            user_id="user-a",
            text="python async await guide",
        ),
        topic=seed_topic,
    )

    def empty_classifier(_event: NormalizedEvent, _model_name: str):
        return None

    stage = TopicRouterStage(
        path_manager=bootstrap.path_manager,
        topic_model_name="topic-model",
        chat_default_model="chat-default",
        classifier=empty_classifier,
        short_memory_stage=ShortMemoryStage(
            path_manager=bootstrap.path_manager,
            vec_loader=lambda _conn: False,
        ),
    )
    event = NormalizedEvent(
        message_id="m-topic-02",
        session_id="session-1",
        scope_id="scope-group-1",
        user_id="user-a",
        text="need help with python await pattern",
    )

    assignment = stage.assign_topic(event)

    assert assignment.topic_id == "topic_python"
    assert assignment.source == "vec_nn"
