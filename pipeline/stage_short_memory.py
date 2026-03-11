from __future__ import annotations

import json
import hashlib
import logging
import sqlite3
from collections.abc import Callable

from .contracts import ImageFacts, NormalizedEvent, ShortMemoryRecord, TopicAssignment
from ..storage.path_manager import StoragePathManager

try:
    from astrbot.api import logger
except ModuleNotFoundError:  # pragma: no cover - fallback for local unit tests.
    logger = logging.getLogger(__name__)

EmbeddingFn = Callable[[str], list[float]]
VecLoaderFn = Callable[[sqlite3.Connection], bool]


class VecUnavailableWarning(RuntimeWarning):
    """Raised when sqlite-vec is unavailable and lexical fallback is enabled."""


class ShortMemoryStage:
    """Persist and recall short memory messages by topic bucket."""

    def __init__(
        self,
        path_manager: StoragePathManager,
        vec_loader: VecLoaderFn | None = None,
        embedding_fn: EmbeddingFn | None = None,
    ) -> None:
        self.path_manager = path_manager
        self.vec_loader = vec_loader or _default_vec_loader
        self.embedding_fn = embedding_fn or _default_embedding
        self.embedding_dimension = len(self.embedding_fn("dimension_probe"))
        if self.embedding_dimension <= 0:
            self.embedding_dimension = 16
        self._vec_checked = False
        self._vec_enabled = False
        self._vec_reason: str = "unknown"

    def append_message(
        self,
        event: NormalizedEvent,
        topic: TopicAssignment,
        image_facts: tuple[ImageFacts, ...] = (),
    ) -> ShortMemoryRecord:
        db_path = self.path_manager.short_memory_bucket_by_key(f"{event.scope_id}:{topic.topic_id}")
        content = self._compose_content(event.text, image_facts)
        created_at = event.created_at

        with sqlite3.connect(db_path) as conn:
            self._ensure_tables(conn)
            self._ensure_vec_runtime(conn)
            conn.execute(
                """
                INSERT INTO messages(message_id, scope_id, topic_id, role, content, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(message_id) DO UPDATE SET
                    scope_id = excluded.scope_id,
                    topic_id = excluded.topic_id,
                    role = excluded.role,
                    content = excluded.content
                """,
                (
                    event.message_id,
                    event.scope_id,
                    topic.topic_id,
                    event.role,
                    content,
                    created_at,
                ),
            )
            message_db_id_row = conn.execute(
                "SELECT id FROM messages WHERE message_id = ?",
                (event.message_id,),
            ).fetchone()
            if message_db_id_row is None:
                raise RuntimeError(f"message row missing for {event.message_id}")
            message_db_id = int(message_db_id_row[0])
            conn.execute(
                """
                INSERT OR IGNORE INTO topic_message_map(topic_id, message_id)
                VALUES (?, ?)
                """,
                (topic.topic_id, event.message_id),
            )
            conn.execute("DELETE FROM message_embeddings WHERE message_id = ?", (event.message_id,))
            embedding_values = self.embedding_fn(content)
            conn.execute(
                """
                INSERT INTO message_embeddings(message_id, scope_id, topic_id, embedding_json, backend)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    event.message_id,
                    event.scope_id,
                    topic.topic_id,
                    json.dumps(embedding_values, ensure_ascii=False),
                    "sqlite_vec" if self.vec_enabled else "lexical_fallback",
                ),
            )
            if self.vec_enabled:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO message_embeddings_vec(
                        message_db_id, scope_id, topic_id, embedding
                    )
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        message_db_id,
                        event.scope_id,
                        topic.topic_id,
                        _serialize_embedding_for_vec0(embedding_values),
                    ),
                )
            conn.commit()

        return ShortMemoryRecord(
            message_id=event.message_id,
            scope_id=event.scope_id,
            topic_id=topic.topic_id,
            role=event.role,
            content=content,
            created_at=created_at,
            metadata={"bucket": db_path.name},
        )

    def recall_recent(
        self,
        scope_id: str,
        topic_id: str,
        limit: int = 8,
    ) -> tuple[ShortMemoryRecord, ...]:
        db_path = self.path_manager.short_memory_bucket_by_key(f"{scope_id}:{topic_id}")
        with sqlite3.connect(db_path) as conn:
            self._ensure_tables(conn)
            rows = conn.execute(
                """
                SELECT message_id, role, content, created_at
                FROM messages
                WHERE scope_id = ? AND topic_id = ?
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (scope_id, topic_id, limit),
            ).fetchall()

        records = [
            ShortMemoryRecord(
                message_id=row[0],
                scope_id=scope_id,
                topic_id=topic_id,
                role=row[1],
                content=row[2],
                created_at=row[3],
            )
            for row in reversed(rows)
        ]
        return tuple(records)

    def recall_by_similarity(
        self,
        scope_id: str,
        topic_id: str,
        query_text: str,
        limit: int = 8,
    ) -> tuple[ShortMemoryRecord, ...]:
        db_path = self.path_manager.short_memory_bucket_by_key(f"{scope_id}:{topic_id}")
        with sqlite3.connect(db_path) as conn:
            self._ensure_tables(conn)
            self._ensure_vec_runtime(conn)
            if self.vec_enabled:
                return self._recall_by_vector(conn, scope_id, topic_id, query_text, limit)
            return self._recall_by_lexical(conn, scope_id, topic_id, query_text, limit)

    def best_similarity_score(
        self,
        scope_id: str,
        topic_id: str,
        query_text: str,
    ) -> float:
        db_path = self.path_manager.short_memory_bucket_by_key(f"{scope_id}:{topic_id}")
        with sqlite3.connect(db_path) as conn:
            self._ensure_tables(conn)
            self._ensure_vec_runtime(conn)
            if self.vec_enabled:
                return self._best_vector_score(conn, scope_id, topic_id, query_text)
            return self._best_lexical_score(conn, scope_id, topic_id, query_text)

    def _compose_content(self, text: str, image_facts: tuple[ImageFacts, ...]) -> str:
        parts = [text.strip()]
        for image_fact in image_facts:
            parts.append(f"[image] {image_fact.summary}")
        return "\n".join(part for part in parts if part)

    def _ensure_tables(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id TEXT NOT NULL UNIQUE,
                scope_id TEXT NOT NULL,
                topic_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS topic_message_map (
                topic_id TEXT NOT NULL,
                message_id TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (topic_id, message_id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS message_embeddings (
                message_id TEXT PRIMARY KEY,
                scope_id TEXT NOT NULL,
                topic_id TEXT NOT NULL,
                embedding_json TEXT NOT NULL,
                backend TEXT NOT NULL DEFAULT 'lexical_fallback',
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_message_embeddings_scope_topic
            ON message_embeddings(scope_id, topic_id)
            """
        )

    def _ensure_vec_runtime(self, conn: sqlite3.Connection) -> None:
        if not self._vec_checked:
            loaded, reason = self._try_load_vec_on_connection(conn)
            self._vec_enabled = loaded
            self._vec_reason = reason
            if not loaded:
                logger.warning("sqlite-vec unavailable, fallback lexical retrieval: %s", reason)
            self._vec_checked = True
        elif self._vec_enabled:
            loaded, reason = self._try_load_vec_on_connection(conn)
            if not loaded:
                self._vec_enabled = False
                self._vec_reason = reason
                logger.warning("sqlite-vec unavailable on new connection, fallback lexical retrieval: %s", reason)

        if self._vec_enabled:
            vec_dimension = max(1, int(self.embedding_dimension))
            conn.execute(
                f"""
                CREATE VIRTUAL TABLE IF NOT EXISTS message_embeddings_vec USING vec0(
                    message_db_id integer primary key,
                    scope_id text partition key,
                    topic_id text partition key,
                    embedding float[{vec_dimension}]
                )
                """
            )

    def _try_load_vec_on_connection(self, conn: sqlite3.Connection) -> tuple[bool, str]:
        try:
            loaded = bool(self.vec_loader(conn))
            if not loaded:
                return False, "sqlite_vec_unavailable:vec loader returned False"
            return True, "sqlite_vec_enabled"
        except Exception as exc:
            return False, f"sqlite_vec_unavailable:{exc}"

    def _recall_by_vector(
        self,
        conn: sqlite3.Connection,
        scope_id: str,
        topic_id: str,
        query_text: str,
        limit: int,
    ) -> tuple[ShortMemoryRecord, ...]:
        query_embedding = _serialize_embedding_for_vec0(self.embedding_fn(query_text))
        rows = conn.execute(
            """
            SELECT message_db_id, distance
            FROM message_embeddings_vec
            WHERE embedding MATCH ?
              AND k = ?
              AND scope_id = ?
              AND topic_id = ?
            ORDER BY distance
            """,
            (query_embedding, max(1, limit), scope_id, topic_id),
        ).fetchall()
        if not rows:
            return ()

        message_db_ids = tuple(int(row[0]) for row in rows[:limit])
        return self._load_records_by_db_ids(conn, scope_id, topic_id, message_db_ids)

    def _best_vector_score(
        self,
        conn: sqlite3.Connection,
        scope_id: str,
        topic_id: str,
        query_text: str,
    ) -> float:
        query_embedding = _serialize_embedding_for_vec0(self.embedding_fn(query_text))
        row = conn.execute(
            """
            SELECT distance
            FROM message_embeddings_vec
            WHERE embedding MATCH ?
              AND k = 1
              AND scope_id = ?
              AND topic_id = ?
            ORDER BY distance
            """,
            (query_embedding, scope_id, topic_id),
        ).fetchone()
        if row is None:
            return 0.0
        distance = float(row[0]) if row[0] is not None else 0.0
        if distance < 0.0:
            distance = 0.0
        return 1.0 / (1.0 + distance)

    def _recall_by_lexical(
        self,
        conn: sqlite3.Connection,
        scope_id: str,
        topic_id: str,
        query_text: str,
        limit: int,
    ) -> tuple[ShortMemoryRecord, ...]:
        query_tokens = set(query_text.lower().split())
        rows = conn.execute(
            """
            SELECT message_id, role, content, created_at
            FROM messages
            WHERE scope_id = ? AND topic_id = ?
            ORDER BY id DESC
            LIMIT 80
            """,
            (scope_id, topic_id),
        ).fetchall()
        scored: list[tuple[str, float]] = []
        for message_id, _, content, _ in rows:
            content_tokens = set(content.lower().split())
            score = _jaccard(query_tokens, content_tokens)
            scored.append((message_id, score))
        scored.sort(key=lambda item: item[1], reverse=True)
        message_ids = tuple(item[0] for item in scored[:limit] if item[1] > 0.0)
        return self._load_records_by_message_ids(conn, scope_id, topic_id, message_ids)

    def _best_lexical_score(
        self,
        conn: sqlite3.Connection,
        scope_id: str,
        topic_id: str,
        query_text: str,
    ) -> float:
        query_tokens = set(query_text.lower().split())
        rows = conn.execute(
            """
            SELECT content
            FROM messages
            WHERE scope_id = ? AND topic_id = ?
            ORDER BY id DESC
            LIMIT 80
            """,
            (scope_id, topic_id),
        ).fetchall()
        best = 0.0
        for row in rows:
            content_tokens = set(str(row[0]).lower().split())
            score = _jaccard(query_tokens, content_tokens)
            if score > best:
                best = score
        return best

    def _load_records_by_db_ids(
        self,
        conn: sqlite3.Connection,
        scope_id: str,
        topic_id: str,
        message_db_ids: tuple[int, ...],
    ) -> tuple[ShortMemoryRecord, ...]:
        if not message_db_ids:
            return ()
        placeholders = ",".join("?" for _ in message_db_ids)
        query = (
            "SELECT message_id, role, content, created_at, id "
            f"FROM messages WHERE scope_id = ? AND topic_id = ? AND id IN ({placeholders})"
        )
        rows = conn.execute(query, (scope_id, topic_id, *message_db_ids)).fetchall()
        rank_index = {message_db_id: idx for idx, message_db_id in enumerate(message_db_ids)}
        rows.sort(key=lambda row: rank_index.get(int(row[4]), len(rank_index)))
        records = [
            ShortMemoryRecord(
                message_id=row[0],
                scope_id=scope_id,
                topic_id=topic_id,
                role=row[1],
                content=row[2],
                created_at=row[3],
            )
            for row in rows
        ]
        return tuple(records)

    def _load_records_by_message_ids(
        self,
        conn: sqlite3.Connection,
        scope_id: str,
        topic_id: str,
        message_ids: tuple[str, ...],
    ) -> tuple[ShortMemoryRecord, ...]:
        if not message_ids:
            return ()
        placeholders = ",".join("?" for _ in message_ids)
        query = (
            "SELECT message_id, role, content, created_at "
            f"FROM messages WHERE scope_id = ? AND topic_id = ? AND message_id IN ({placeholders})"
        )
        rows = conn.execute(query, (scope_id, topic_id, *message_ids)).fetchall()
        records = [
            ShortMemoryRecord(
                message_id=row[0],
                scope_id=scope_id,
                topic_id=topic_id,
                role=row[1],
                content=row[2],
                created_at=row[3],
            )
            for row in sorted(rows, key=lambda item: item[3])
        ]
        return tuple(records)

    @property
    def vec_enabled(self) -> bool:
        return self._vec_enabled

    @property
    def vec_reason(self) -> str:
        return self._vec_reason


def _default_vec_loader(conn: sqlite3.Connection) -> bool:
    try:
        import sqlite_vec  # type: ignore
    except ModuleNotFoundError:
        return False
    try:
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
    except Exception:
        return False
    return True


def _default_embedding(text: str, dimension: int = 16) -> list[float]:
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    values = [digest[idx] / 255.0 for idx in range(dimension)]
    return values


def _jaccard(tokens_a: set[str], tokens_b: set[str]) -> float:
    if not tokens_a or not tokens_b:
        return 0.0
    union = tokens_a | tokens_b
    if not union:
        return 0.0
    return len(tokens_a & tokens_b) / len(union)


def _serialize_embedding_for_vec0(values: list[float]) -> str:
    # sqlite-vec accepts JSON vector literals in INSERT and MATCH clauses.
    return json.dumps([float(item) for item in values], ensure_ascii=False)
