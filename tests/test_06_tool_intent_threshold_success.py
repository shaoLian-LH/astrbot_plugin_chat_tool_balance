from pipeline.contracts import NormalizedEvent
from pipeline.stage_tool_intent import ToolIntentStage


def test_tool_intent_hit_and_injection_success():
    stage = ToolIntentStage(
        tool_intent_model="",
        chat_default_model="chat-default-model",
        threshold=0.7,
    )
    event = NormalizedEvent(
        message_id="m-intent-01",
        session_id="session-1",
        scope_id="scope-group-1",
        user_id="user-a",
        text="请帮我查询今天上海天气",
    )

    decision = stage.process(event)

    assert decision.route == "tool"
    assert decision.confidence >= 0.7
    assert decision.model_name == "chat-default-model"
    assert decision.prompt_injection
    assert decision.reason_code == "keyword_hit"


def test_tool_intent_chat_path_no_injection_success():
    stage = ToolIntentStage(
        tool_intent_model="tool-intent-model",
        chat_default_model="chat-default-model",
        threshold=0.7,
    )
    event = NormalizedEvent(
        message_id="m-intent-02",
        session_id="session-1",
        scope_id="scope-group-1",
        user_id="user-a",
        text="今天过得还不错",
    )

    decision = stage.process(event)

    assert decision.route == "chat"
    assert decision.confidence < 0.7
    assert decision.model_name == "tool-intent-model"
    assert decision.prompt_injection == ""
