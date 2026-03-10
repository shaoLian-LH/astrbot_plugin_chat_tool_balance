import sqlite3

from pipeline.contracts import NormalizedEvent
from pipeline.stage_image_ocr import ImageOCRStage
from storage.bootstrap import initialize_storage


def test_image_ocr_failure_returns_placeholder_and_skips_cache_success(tmp_path):
    bootstrap = initialize_storage(base_dir=str(tmp_path / "plugin_data"), bucket_count=10)
    call_count = 0

    def failing_describe_image(_source_url: str, _event: NormalizedEvent):
        nonlocal call_count
        call_count += 1
        raise RuntimeError("ocr_service_down")

    stage = ImageOCRStage(
        path_manager=bootstrap.path_manager,
        describe_image=failing_describe_image,
    )
    image_url = "https://example.com/failure.png"
    event = NormalizedEvent(
        message_id="m-image-failure-1",
        session_id="session-ocr-failure",
        scope_id="scope-ocr-failure",
        user_id="user-ocr",
        image_urls=(image_url,),
    )

    first = stage.process(event)
    second = stage.process(event)

    assert len(first) == 1
    assert len(second) == 1
    assert first[0].status == "ocr_failed"
    assert first[0].cache_hit is False
    assert first[0].description == "image description unavailable"
    assert "ocr_service_down" in str(first[0].metadata.get("error", ""))
    assert second[0].status == "ocr_failed"
    assert call_count == 2

    content_hash = stage._sha256(image_url)
    db_path = bootstrap.path_manager.image_cache_bucket_by_key(f"{content_hash}:{content_hash}")
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT COUNT(1)
            FROM image_descriptions
            WHERE content_hash = ? AND source_url_hash = ?
            """,
            (content_hash, content_hash),
        ).fetchone()
    assert row is not None
    assert int(row[0]) == 0
