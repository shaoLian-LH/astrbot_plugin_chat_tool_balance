import sqlite3

from astrbot_plugin_chat_tool_balance.pipeline.contracts import NormalizedEvent
from astrbot_plugin_chat_tool_balance.pipeline.stage_topic_router import TopicRouterStage
from astrbot_plugin_chat_tool_balance.storage.bootstrap import initialize_storage


def test_topic_scope_isolation_for_same_topic_id_success(tmp_path):
    bootstrap = initialize_storage(base_dir=str(tmp_path / "plugin_data"), bucket_count=10)

    stage = TopicRouterStage(
        path_manager=bootstrap.path_manager,
        topic_model_name="topic-model",
        chat_default_model="chat-default",
        classifier=lambda _event, _model_name: ("weather", 0.95),
    )

    event_scope_a = NormalizedEvent(
        message_id="m-topic-a",
        session_id="session-a",
        scope_id="group-a",
        user_id="user-a",
        text="group-a weather topic",
    )
    event_scope_b = NormalizedEvent(
        message_id="m-topic-b",
        session_id="session-b",
        scope_id="group-b",
        user_id="user-b",
        text="group-b weather topic",
    )

    assignment_a = stage.assign_topic(event_scope_a)
    assignment_b = stage.assign_topic(event_scope_b)

    assert assignment_a.topic_id == "weather"
    assert assignment_b.topic_id == "weather"
    assert assignment_a.scope_id == "group-a"
    assert assignment_b.scope_id == "group-b"

    with sqlite3.connect(bootstrap.path_manager.core_db_path()) as conn:
        topic_rows = conn.execute(
            """
            SELECT scope_id, topic_id
            FROM topics
            WHERE topic_id = 'weather'
            ORDER BY scope_id
            """
        ).fetchall()
        assert topic_rows == [("group-a", "weather"), ("group-b", "weather")]
