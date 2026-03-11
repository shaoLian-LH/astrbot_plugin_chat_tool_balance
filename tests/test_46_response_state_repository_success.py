from storage.bootstrap import initialize_storage
from storage.response_state_repository import ResponseStateRepository


def _build_repository(tmp_path) -> ResponseStateRepository:
    base_dir = str(tmp_path / "plugin_data")
    bootstrap = initialize_storage(base_dir=base_dir, bucket_count=10)
    return ResponseStateRepository(path_manager=bootstrap.path_manager)


def test_response_state_repository_upsert_then_read_hit_success(tmp_path):
    repository = _build_repository(tmp_path)

    assert repository.get_previous_response_id(scope_id="scope-a", topic_id="topic-a") is None

    repository.upsert_state(
        scope_id="scope-a",
        topic_id="topic-a",
        previous_response_id="resp_001",
        provider_id="provider-openai",
        model_name="gpt-4.1",
        updated_at="2026-03-10T00:00:00+00:00",
    )

    assert repository.get_previous_response_id(scope_id="scope-a", topic_id="topic-a") == "resp_001"


def test_response_state_repository_fallback_skip_write_keeps_state_success(tmp_path):
    repository = _build_repository(tmp_path)

    repository.upsert_state(
        scope_id="scope-a",
        topic_id="topic-a",
        previous_response_id="resp_001",
        provider_id="provider-openai",
        model_name="gpt-4.1",
        updated_at="2026-03-10T00:00:00+00:00",
    )

    before = repository.get_previous_response_id(scope_id="scope-a", topic_id="topic-a")
    assert before == "resp_001"

    after = repository.get_previous_response_id(scope_id="scope-a", topic_id="topic-a")
    assert after == before


def test_response_state_repository_delete_then_read_empty_success(tmp_path):
    repository = _build_repository(tmp_path)

    repository.upsert_state(
        scope_id="scope-a",
        topic_id="topic-a",
        previous_response_id="resp_001",
        provider_id="provider-openai",
        model_name="gpt-4.1",
        updated_at="2026-03-10T00:00:00+00:00",
    )
    assert repository.get_previous_response_id(scope_id="scope-a", topic_id="topic-a") == "resp_001"

    assert repository.delete_state(scope_id="scope-a", topic_id="topic-a") == 1
    assert repository.get_previous_response_id(scope_id="scope-a", topic_id="topic-a") is None
    assert repository.delete_by_scope_topic(scope_id="scope-a", topic_id="topic-a") == 0


def test_response_state_repository_same_key_last_upsert_wins_success(tmp_path):
    repository = _build_repository(tmp_path)

    repository.upsert_state(
        scope_id="scope-a",
        topic_id="topic-a",
        previous_response_id="resp_001",
        provider_id="provider-openai",
        model_name="gpt-4.1",
        updated_at="2026-03-10T00:00:00+00:00",
    )
    repository.upsert_state(
        scope_id="scope-a",
        topic_id="topic-a",
        previous_response_id="resp_002",
        provider_id="provider-openai-new",
        model_name="gpt-4.1-mini",
        updated_at="2026-03-10T00:00:05+00:00",
    )

    state = repository.get_state(scope_id="scope-a", topic_id="topic-a")
    assert state is not None
    assert state.previous_response_id == "resp_002"
    assert state.provider_id == "provider-openai-new"
    assert state.model_name == "gpt-4.1-mini"
    assert state.updated_at == "2026-03-10T00:00:05+00:00"

