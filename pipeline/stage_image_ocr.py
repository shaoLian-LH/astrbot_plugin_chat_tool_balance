from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pipeline.contracts import ImageFacts, NormalizedEvent
from storage.path_manager import StoragePathManager

ImageDescribeFn = Callable[[str, NormalizedEvent], tuple[str, dict[str, Any]]]


class ImageOCRStage:
    """Resolve image facts with cache-first behavior."""

    def __init__(
        self,
        path_manager: StoragePathManager,
        describe_image: ImageDescribeFn | None = None,
    ) -> None:
        self.path_manager = path_manager
        self.describe_image = describe_image or self._default_describe_image

    def process(self, event: NormalizedEvent) -> tuple[ImageFacts, ...]:
        facts: list[ImageFacts] = []
        for source_url in event.iter_non_empty_image_urls():
            facts.append(self._process_single(source_url, event))
        return tuple(facts)

    def _process_single(self, source_url: str, event: NormalizedEvent) -> ImageFacts:
        content_hash = self._sha256(source_url)
        source_url_hash = self._sha256(source_url)
        bucket_key = f"{content_hash}:{source_url_hash}"
        db_path = self.path_manager.image_cache_bucket_by_key(bucket_key)

        cache_result = self._read_cache(db_path, content_hash, source_url_hash)
        if cache_result is not None:
            description, metadata = cache_result
            return ImageFacts(
                source_url=source_url,
                content_hash=content_hash,
                source_url_hash=source_url_hash,
                description=description,
                metadata=metadata,
                cache_hit=True,
                status="cache_hit",
            )

        try:
            description, metadata = self.describe_image(source_url, event)
        except Exception as exc:
            return ImageFacts(
                source_url=source_url,
                content_hash=content_hash,
                source_url_hash=source_url_hash,
                description="image description unavailable",
                metadata={"error": str(exc)},
                cache_hit=False,
                status="ocr_failed",
            )

        self._write_cache(
            db_path=db_path,
            content_hash=content_hash,
            source_url_hash=source_url_hash,
            description=description,
            metadata=metadata,
        )
        return ImageFacts(
            source_url=source_url,
            content_hash=content_hash,
            source_url_hash=source_url_hash,
            description=description,
            metadata=metadata,
            cache_hit=False,
            status="generated",
        )

    def _read_cache(
        self,
        db_path: Path,
        content_hash: str,
        source_url_hash: str,
    ) -> tuple[str, dict[str, Any]] | None:
        with sqlite3.connect(db_path) as conn:
            self._ensure_tables(conn)
            row = conn.execute(
                """
                SELECT description, metadata_json
                FROM image_descriptions
                WHERE content_hash = ? AND source_url_hash = ?
                """,
                (content_hash, source_url_hash),
            ).fetchone()
            if row is None:
                return None
            conn.execute(
                """
                INSERT INTO image_access_log (content_hash, source_url_hash)
                VALUES (?, ?)
                """,
                (content_hash, source_url_hash),
            )
            conn.commit()
            return row[0], self._load_metadata(row[1])

    def _write_cache(
        self,
        db_path: Path,
        content_hash: str,
        source_url_hash: str,
        description: str,
        metadata: dict[str, Any],
    ) -> None:
        with sqlite3.connect(db_path) as conn:
            self._ensure_tables(conn)
            conn.execute(
                """
                INSERT INTO image_descriptions(
                    content_hash, source_url_hash, description, metadata_json
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(content_hash, source_url_hash) DO UPDATE SET
                    description = excluded.description,
                    metadata_json = excluded.metadata_json,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    content_hash,
                    source_url_hash,
                    description,
                    json.dumps(metadata, ensure_ascii=False, sort_keys=True),
                ),
            )
            conn.execute(
                """
                INSERT INTO image_access_log (content_hash, source_url_hash)
                VALUES (?, ?)
                """,
                (content_hash, source_url_hash),
            )
            conn.commit()

    def _default_describe_image(
        self,
        source_url: str,
        _event: NormalizedEvent,
    ) -> tuple[str, dict[str, Any]]:
        return (
            f"image from {source_url}",
            {"provider": "fallback", "source_url": source_url},
        )

    def _ensure_tables(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS image_descriptions (
                content_hash TEXT NOT NULL,
                source_url_hash TEXT NOT NULL,
                description TEXT NOT NULL,
                metadata_json TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (content_hash, source_url_hash)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS image_access_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content_hash TEXT NOT NULL,
                source_url_hash TEXT NOT NULL,
                access_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

    @staticmethod
    def _load_metadata(raw_metadata: str | None) -> dict[str, Any]:
        if not raw_metadata:
            return {}
        try:
            loaded = json.loads(raw_metadata)
        except json.JSONDecodeError:
            return {}
        return loaded if isinstance(loaded, dict) else {}

    @staticmethod
    def _sha256(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()
