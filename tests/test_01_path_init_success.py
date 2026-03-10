from storage.path_manager import StoragePathManager


def test_path_init_success(tmp_path):
    base_dir = tmp_path / "plugin_data"
    path_manager = StoragePathManager(str(base_dir), 10)

    path_manager.ensure_directories()

    expected_dirs = [
        base_dir,
        base_dir / "core",
        base_dir / "short_memory",
        base_dir / "summary",
        base_dir / "image",
        base_dir / "image" / "tmp",
    ]
    for target in expected_dirs:
        assert target.exists()
        assert target.is_dir()

