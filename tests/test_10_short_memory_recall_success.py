from pipeline.contracts import ImageFacts, NormalizedEvent, TopicAssignment
from pipeline.stage_short_memory import ShortMemoryStage
from storage.bootstrap import initialize_storage


def test_short_memory_write_and_recall_success(tmp_path):
    bootstrap = initialize_storage(base_dir=str(tmp_path / "plugin_data"), bucket_count=10)
    stage = ShortMemoryStage(path_manager=bootstrap.path_manager)
    topic = TopicAssignment(
        topic_id="topic-1",
        session_id="session-1",
        scope_id="scope-group-1",
        source="new_topic",
        confidence=0.8,
        model_name="",
        title="topic one",
    )
    image_fact = ImageFacts(
        source_url="https://example.com/image.png",
        content_hash="h1",
        source_url_hash="u1",
        description="diagram about api flow",
        cache_hit=False,
        status="generated",
    )
    event = NormalizedEvent(
        message_id="m-memory-01",
        session_id="session-1",
        scope_id="scope-group-1",
        user_id="user-a",
        text="请记录这个设计思路",
    )

    stage.append_message(event=event, topic=topic, image_facts=(image_fact,))
    recalled = stage.recall_recent(scope_id="scope-group-1", topic_id="topic-1", limit=5)

    assert len(recalled) == 1
    assert recalled[0].message_id == "m-memory-01"
    assert "[image] diagram about api flow" in recalled[0].content
