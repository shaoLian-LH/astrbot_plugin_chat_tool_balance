import pytest

from pipeline.contracts import NormalizedEvent, TopicAssignment
from pipeline.stage_short_memory import ShortMemoryStage
from storage.bootstrap import initialize_storage


def test_short_memory_vec_real_query_success(tmp_path):
    sqlite_vec = pytest.importorskip("sqlite_vec")

    bootstrap = initialize_storage(base_dir=str(tmp_path / "plugin_data"), bucket_count=10)

    def vec_loader(conn) -> bool:
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        return True

    def embedding_fn(text: str) -> list[float]:
        if "python" in text.lower():
            return [1.0, 0.0, 0.0, 0.0]
        if "sql" in text.lower():
            return [0.0, 1.0, 0.0, 0.0]
        return [0.0, 0.0, 1.0, 0.0]

    stage = ShortMemoryStage(
        path_manager=bootstrap.path_manager,
        vec_loader=vec_loader,
        embedding_fn=embedding_fn,
    )
    topic = TopicAssignment(
        topic_id="topic-vec-real",
        session_id="session-1",
        scope_id="scope-group-1",
        source="new_topic",
        confidence=0.9,
        model_name="",
        title="vec-real-topic",
    )
    stage.append_message(
        event=NormalizedEvent(
            message_id="m-vec-real-1",
            session_id="session-1",
            scope_id="scope-group-1",
            user_id="u1",
            text="python coroutine guide",
        ),
        topic=topic,
    )
    stage.append_message(
        event=NormalizedEvent(
            message_id="m-vec-real-2",
            session_id="session-1",
            scope_id="scope-group-1",
            user_id="u1",
            text="sql tuning tips",
        ),
        topic=topic,
    )

    recalled = stage.recall_by_similarity(
        scope_id="scope-group-1",
        topic_id="topic-vec-real",
        query_text="need python async tips",
        limit=1,
    )

    assert stage.vec_enabled is True
    assert stage.vec_reason == "sqlite_vec_enabled"
    assert len(recalled) == 1
    assert recalled[0].message_id == "m-vec-real-1"
