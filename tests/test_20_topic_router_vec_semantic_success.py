import pytest
import sqlite3

from pipeline.contracts import NormalizedEvent, TopicAssignment
from pipeline.stage_short_memory import ShortMemoryStage
from pipeline.stage_topic_router import TopicRouterStage
from storage.bootstrap import initialize_storage


def test_topic_router_vec_semantic_match_success(tmp_path):
    sqlite_vec = pytest.importorskip("sqlite_vec")
    bootstrap = initialize_storage(base_dir=str(tmp_path / "plugin_data"), bucket_count=10)

    with sqlite3.connect(bootstrap.path_manager.core_db_path()) as conn:
        conn.execute(
            "INSERT INTO topics(scope_id, topic_id, title) VALUES (?, ?, ?)",
            ("scope-group-1", "topic_python_semantic", "finance"),
        )
        conn.commit()

    def vec_loader(conn) -> bool:
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        return True

    def embedding_fn(text: str) -> list[float]:
        lowered = text.lower()
        if "python" in lowered or "serpent" in lowered:
            return [1.0, 0.0, 0.0, 0.0]
        if "sql" in lowered or "database" in lowered:
            return [0.0, 1.0, 0.0, 0.0]
        return [0.0, 0.0, 1.0, 0.0]

    short_memory_stage = ShortMemoryStage(
        path_manager=bootstrap.path_manager,
        vec_loader=vec_loader,
        embedding_fn=embedding_fn,
    )
    short_memory_stage.append_message(
        event=NormalizedEvent(
            message_id="m-semantic-seed",
            session_id="session-1",
            scope_id="scope-group-1",
            user_id="user-a",
            text="python coroutine primer",
        ),
        topic=TopicAssignment(
            topic_id="topic_python_semantic",
            session_id="session-1",
            scope_id="scope-group-1",
            source="new_topic",
            confidence=0.9,
            model_name="",
            title="finance",
        ),
    )

    stage = TopicRouterStage(
        path_manager=bootstrap.path_manager,
        topic_model_name="topic-model",
        chat_default_model="chat-default",
        classifier=lambda _event, _model_name: None,
        vec_min_score=0.3,
        short_memory_stage=short_memory_stage,
    )
    event = NormalizedEvent(
        message_id="m-semantic-query",
        session_id="session-1",
        scope_id="scope-group-1",
        user_id="user-b",
        text="serpent async tutorial",
    )

    assignment = stage.assign_topic(event)

    assert assignment.topic_id == "topic_python_semantic"
    assert assignment.source == "vec_nn"
