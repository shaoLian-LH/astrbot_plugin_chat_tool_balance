from __future__ import annotations

import hashlib
from pathlib import Path

DEFAULT_BASE_DIR = "/data/plugin_data/astrbot_plugin_chat_tool_balance"
DEFAULT_BUCKET_COUNT = 10


class StoragePathManager:
    def __init__(self, base_dir: str = DEFAULT_BASE_DIR, bucket_count: int = DEFAULT_BUCKET_COUNT):
        self.base_dir = Path(base_dir)
        self.bucket_count = bucket_count if bucket_count > 0 else DEFAULT_BUCKET_COUNT

    def ensure_directories(self) -> None:
        for folder in self.required_directories():
            folder.mkdir(parents=True, exist_ok=True)

    def required_directories(self) -> list[Path]:
        image_dir = self.base_dir / "image"
        return [
            self.base_dir,
            self.base_dir / "core",
            self.base_dir / "short_memory",
            self.base_dir / "summary",
            image_dir,
            image_dir / "tmp",
        ]

    def route_bucket(self, key: str) -> int:
        digest = hashlib.sha256(str(key).encode("utf-8")).digest()
        digest_value = int.from_bytes(digest[:8], byteorder="big", signed=False)
        return digest_value % self.bucket_count

    def short_memory_bucket_by_key(self, key: str) -> Path:
        return self.short_memory_bucket_path(self.route_bucket(key))

    def image_cache_bucket_by_key(self, key: str) -> Path:
        return self.image_cache_bucket_path(self.route_bucket(key))

    def core_db_path(self) -> Path:
        return self.base_dir / "core" / "core_state.db"

    def summary_jobs_db_path(self) -> Path:
        return self.base_dir / "summary" / "summary_jobs.db"

    def short_memory_bucket_paths(self) -> list[Path]:
        return [self.short_memory_bucket_path(idx) for idx in range(self.bucket_count)]

    def image_cache_bucket_paths(self) -> list[Path]:
        return [self.image_cache_bucket_path(idx) for idx in range(self.bucket_count)]

    def short_memory_bucket_path(self, bucket_index: int) -> Path:
        bucket_index = bucket_index % self.bucket_count
        return self.base_dir / "short_memory" / f"bucket_{bucket_index:02d}.db"

    def image_cache_bucket_path(self, bucket_index: int) -> Path:
        bucket_index = bucket_index % self.bucket_count
        return self.base_dir / "image" / f"cache_{bucket_index:02d}.db"

