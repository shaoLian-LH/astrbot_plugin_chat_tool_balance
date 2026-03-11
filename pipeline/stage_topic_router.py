from __future__ import annotations

import re
import sqlite3
import uuid
from collections.abc import Callable

from .contracts import NormalizedEvent, TopicAssignment
from .stage_short_memory import ShortMemoryStage
from ..storage.path_manager import StoragePathManager

TopicClassifier = Callable[[NormalizedEvent, str], tuple[str | None, float] | str | None]


class TopicRouterStage:
    """Resolve exactly one topic using model -> rule -> vec nn -> new topic."""

    def __init__(
        self,
        path_manager: StoragePathManager,
        topic_model_name: str,
        chat_default_model: str,
        classifier: TopicClassifier | None = None,
        vec_min_score: float = 0.2,
        vec_scan_limit: int = 60,
        short_memory_stage: ShortMemoryStage | None = None,
    ) -> None:
        self.path_manager = path_manager
        self.model_name = (topic_model_name or "").strip() or (chat_default_model or "").strip()
        self.classifier = classifier
        self.vec_min_score = vec_min_score
        self.vec_scan_limit = vec_scan_limit
        self.short_memory_stage = short_memory_stage or ShortMemoryStage(path_manager=path_manager)

    def assign_topic(self, event: NormalizedEvent) -> TopicAssignment:
        model_topic = self._route_model(event)
        if model_topic is not None:
            topic_id, title, confidence = model_topic
            self._bind_session_topic(event.session_id, event.scope_id, topic_id)
            return TopicAssignment(
                topic_id=topic_id,
                session_id=event.session_id,
                scope_id=event.scope_id,
                source="model_classify",
                confidence=confidence,
                model_name=self.model_name,
                title=title,
            )

        rule_topic = self._route_rule(event)
        if rule_topic is not None:
            topic_id, title, confidence = rule_topic
            self._bind_session_topic(event.session_id, event.scope_id, topic_id)
            return TopicAssignment(
                topic_id=topic_id,
                session_id=event.session_id,
                scope_id=event.scope_id,
                source="rule_match",
                confidence=confidence,
                model_name=self.model_name,
                title=title,
            )

        vec_topic = self._route_vec_nn(event)
        if vec_topic is not None:
            topic_id, title, confidence = vec_topic
            self._bind_session_topic(event.session_id, event.scope_id, topic_id)
            return TopicAssignment(
                topic_id=topic_id,
                session_id=event.session_id,
                scope_id=event.scope_id,
                source="vec_nn",
                confidence=confidence,
                model_name=self.model_name,
                title=title,
            )

        topic_id = self._new_topic_id()
        title = self._topic_title(event.text)
        self._upsert_topic(topic_id, event.scope_id, title)
        self._bind_session_topic(event.session_id, event.scope_id, topic_id)
        return TopicAssignment(
            topic_id=topic_id,
            session_id=event.session_id,
            scope_id=event.scope_id,
            source="new_topic",
            confidence=0.51,
            model_name=self.model_name,
            title=title,
        )

    def _route_model(self, event: NormalizedEvent) -> tuple[str, str, float] | None:
        if self.classifier is None:
            return None
        try:
            result = self.classifier(event, self.model_name)
        except Exception:
            return None

        topic_id = ""
        confidence = 0.8
        if isinstance(result, tuple):
            candidate, candidate_confidence = result
            topic_id = self._normalized_topic_id(candidate or "")
            confidence = float(candidate_confidence)
        else:
            topic_id = self._normalized_topic_id(result or "")
        if not topic_id:
            return None

        title = self._topic_title(event.text)
        existed = self._fetch_topic(topic_id, event.scope_id)
        if existed is not None:
            title = existed[1]
        self._upsert_topic(topic_id, event.scope_id, title)
        return topic_id, title, confidence

    def _route_rule(self, event: NormalizedEvent) -> tuple[str, str, float] | None:
        event_tokens = _tokenize(event.text)
        if not event_tokens:
            return None
        best: tuple[str, str, float] | None = None

        with sqlite3.connect(self.path_manager.core_db_path()) as conn:
            self._ensure_core_tables(conn)
            rows = conn.execute(
                """
                SELECT topic_id, title
                FROM topics
                WHERE scope_id = ?
                """,
                (event.scope_id,),
            ).fetchall()

        for topic_id, title in rows:
            score = _overlap_score(event_tokens, _tokenize(title))
            if score <= 0:
                continue
            if best is None or score > best[2]:
                best = (topic_id, title, score)

        if best is None:
            return None
        return best

    def _route_vec_nn(self, event: NormalizedEvent) -> tuple[str, str, float] | None:
        if not event.text.strip():
            return None
        with sqlite3.connect(self.path_manager.core_db_path()) as conn:
            self._ensure_core_tables(conn)
            candidates = conn.execute(
                """
                SELECT topic_id, title
                FROM topics
                WHERE scope_id = ?
                """,
                (event.scope_id,),
            ).fetchall()
        if not candidates:
            return None

        best_topic_id = ""
        best_title = ""
        best_score = 0.0
        for topic_id, title in candidates:
            score = self.short_memory_stage.best_similarity_score(
                scope_id=event.scope_id,
                topic_id=topic_id,
                query_text=event.text,
            )
            if score > best_score:
                best_score = score
                best_topic_id = topic_id
                best_title = title

        if not best_topic_id or best_score < self.vec_min_score:
            return None
        return best_topic_id, best_title, best_score

    def _fetch_topic(self, topic_id: str, scope_id: str) -> tuple[str, str, str] | None:
        with sqlite3.connect(self.path_manager.core_db_path()) as conn:
            self._ensure_core_tables(conn)
            row = conn.execute(
                """
                SELECT topic_id, title, scope_id
                FROM topics
                WHERE topic_id = ? AND scope_id = ?
                """,
                (topic_id, scope_id),
            ).fetchone()
        return row

    def _upsert_topic(self, topic_id: str, scope_id: str, title: str) -> None:
        with sqlite3.connect(self.path_manager.core_db_path()) as conn:
            self._ensure_core_tables(conn)
            conn.execute(
                """
                INSERT INTO topics(scope_id, topic_id, title)
                VALUES (?, ?, ?)
                ON CONFLICT(scope_id, topic_id) DO UPDATE SET
                    title = excluded.title,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (scope_id, topic_id, title),
            )
            conn.commit()

    def _bind_session_topic(self, session_id: str, scope_id: str, topic_id: str) -> None:
        with sqlite3.connect(self.path_manager.core_db_path()) as conn:
            self._ensure_core_tables(conn)
            conn.execute(
                """
                INSERT INTO session_topic_map(session_id, scope_id, topic_id)
                VALUES (?, ?, ?)
                ON CONFLICT(session_id, scope_id) DO UPDATE SET
                    topic_id = excluded.topic_id,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (session_id, scope_id, topic_id),
            )
            conn.commit()

    def _new_topic_id(self) -> str:
        return f"topic_{uuid.uuid4().hex[:12]}"

    @staticmethod
    def _topic_title(text: str) -> str:
        compact = " ".join(text.strip().split())
        return compact[:32] if compact else "untitled-topic"

    @staticmethod
    def _normalized_topic_id(candidate: str) -> str:
        cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "_", candidate.strip().lower())
        return cleaned[:64]

    def _ensure_core_tables(self, conn: sqlite3.Connection) -> None:
        self._ensure_topics_table_scope_pk(conn)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS session_topic_map (
                session_id TEXT NOT NULL,
                scope_id TEXT NOT NULL,
                topic_id TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (session_id, scope_id)
            )
            """
        )

    def _ensure_topics_table_scope_pk(self, conn: sqlite3.Connection) -> None:
        if not _table_exists(conn, "topics"):
            conn.execute(
                """
                CREATE TABLE topics (
                    scope_id TEXT NOT NULL,
                    topic_id TEXT NOT NULL,
                    title TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (scope_id, topic_id)
                )
                """
            )
            return

        pk_columns = _primary_key_columns(conn, "topics")
        if pk_columns == ("scope_id", "topic_id"):
            return

        conn.execute("ALTER TABLE topics RENAME TO topics_legacy")
        conn.execute(
            """
            CREATE TABLE topics (
                scope_id TEXT NOT NULL,
                topic_id TEXT NOT NULL,
                title TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (scope_id, topic_id)
            )
            """
        )
        conn.execute(
            """
            INSERT INTO topics(scope_id, topic_id, title, created_at, updated_at)
            SELECT scope_id, topic_id, title, created_at, updated_at
            FROM topics_legacy
            """
        )
        conn.execute("DROP TABLE topics_legacy")

def _tokenize(text: str) -> set[str]:
    lowered = text.lower()
    tokens = set(re.findall(r"[a-z0-9]+", lowered))
    for chunk in re.findall(r"[\u4e00-\u9fff]+", text):
        tokens.add(chunk)
        tokens.update(chunk)
        if len(chunk) > 1:
            for idx in range(len(chunk) - 1):
                tokens.add(chunk[idx : idx + 2])
    return {token for token in tokens if token.strip()}


def _overlap_score(a_tokens: set[str], b_tokens: set[str]) -> float:
    if not a_tokens or not b_tokens:
        return 0.0
    intersection = len(a_tokens & b_tokens)
    union = len(a_tokens | b_tokens)
    return intersection / union if union else 0.0


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def _primary_key_columns(conn: sqlite3.Connection, table_name: str) -> tuple[str, ...]:
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    pk_rows = sorted((row for row in rows if int(row[5]) > 0), key=lambda row: int(row[5]))
    return tuple(row[1] for row in pk_rows)
