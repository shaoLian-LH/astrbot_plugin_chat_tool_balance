import json
from dataclasses import asdict

from pipeline.contracts import NormalizedEvent
from pipeline.stage_context_builder import ContextBuilderStage
from pipeline.stage_image_ocr import ImageOCRStage
from pipeline.stage_short_memory import ShortMemoryStage
from pipeline.stage_tool_intent import ToolIntentStage
from pipeline.stage_topic_router import TopicRouterStage
from storage.bootstrap import initialize_storage


def test_pipeline_chain_to_context_success(tmp_path):
    bootstrap = initialize_storage(base_dir=str(tmp_path / "plugin_data"), bucket_count=10)

    image_stage = ImageOCRStage(
        path_manager=bootstrap.path_manager,
        describe_image=lambda _url, _event: ("mountain landscape screenshot", {"provider": "mock"}),
    )
    tool_intent_stage = ToolIntentStage(
        tool_intent_model="tool-model",
        chat_default_model="chat-default",
        threshold=0.7,
    )
    topic_router_stage = TopicRouterStage(
        path_manager=bootstrap.path_manager,
        topic_model_name="topic-model",
        chat_default_model="chat-default",
        classifier=lambda _event, _model: None,
    )
    short_memory_stage = ShortMemoryStage(path_manager=bootstrap.path_manager)
    context_builder_stage = ContextBuilderStage()

    event = NormalizedEvent(
        message_id="m-chain-01",
        session_id="session-1",
        scope_id="scope-group-1",
        user_id="user-a",
        text="请帮我查询这张图里的天气信息",
        image_urls=("https://example.com/weather.png",),
    )

    image_facts = image_stage.process(event)
    decision = tool_intent_stage.process(event, image_facts=image_facts)
    topic = topic_router_stage.assign_topic(event)
    short_memory_stage.append_message(event=event, topic=topic, image_facts=image_facts)
    recalled = short_memory_stage.recall_recent(scope_id=event.scope_id, topic_id=topic.topic_id)
    context_packet = context_builder_stage.build(
        event=event,
        topic=topic,
        tool_intent=decision,
        image_facts=image_facts,
        short_memory=recalled,
    )

    assert decision.route == "tool"
    assert len(image_facts) == 1
    assert len(recalled) == 1
    assert context_packet.topic.topic_id == topic.topic_id
    assert "mountain landscape screenshot" in context_packet.rendered_context
    assert context_packet.context_messages
    json.dumps(asdict(context_packet))
