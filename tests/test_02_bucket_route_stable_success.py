from storage.path_manager import StoragePathManager


def test_bucket_route_stable_success(tmp_path):
    path_manager = StoragePathManager(str(tmp_path / "plugin_data"), 10)
    key = "group-123-topic-xyz"

    first_bucket = path_manager.route_bucket(key)
    for _ in range(20):
        assert path_manager.route_bucket(key) == first_bucket

    assert 0 <= first_bucket < 10


def test_bucket_path_naming_success(tmp_path):
    path_manager = StoragePathManager(str(tmp_path / "plugin_data"), 10)

    short_memory_paths = path_manager.short_memory_bucket_paths()
    image_paths = path_manager.image_cache_bucket_paths()

    assert len(short_memory_paths) == 10
    assert len(image_paths) == 10
    assert short_memory_paths[0].name == "bucket_00.db"
    assert short_memory_paths[9].name == "bucket_09.db"
    assert image_paths[0].name == "cache_00.db"
    assert image_paths[9].name == "cache_09.db"

