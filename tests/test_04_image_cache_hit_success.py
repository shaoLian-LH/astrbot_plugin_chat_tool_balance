from astrbot_plugin_chat_tool_balance.pipeline.contracts import NormalizedEvent
from astrbot_plugin_chat_tool_balance.pipeline.stage_image_ocr import ImageOCRStage
from astrbot_plugin_chat_tool_balance.storage.bootstrap import initialize_storage


def test_image_cache_hit_success(tmp_path):
    bootstrap = initialize_storage(base_dir=str(tmp_path / "plugin_data"), bucket_count=10)
    call_count = 0

    def fake_describe_image(source_url: str, _event: NormalizedEvent):
        nonlocal call_count
        call_count += 1
        return f"mock description: {source_url}", {"provider": "unit-test"}

    stage = ImageOCRStage(
        path_manager=bootstrap.path_manager,
        describe_image=fake_describe_image,
    )
    event = NormalizedEvent(
        message_id="m-image-01",
        session_id="session-1",
        scope_id="scope-group-1",
        user_id="user-a",
        text="看一下这个图片",
        image_urls=("https://example.com/a.png",),
    )

    first = stage.process(event)
    second = stage.process(event)

    assert len(first) == 1
    assert len(second) == 1
    assert first[0].cache_hit is False
    assert second[0].cache_hit is True
    assert second[0].description == first[0].description
    assert call_count == 1
