from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

from .path_manager import StoragePathManager


@dataclass(frozen=True)
class ResponseStateRecord:
    scope_id: str
    topic_id: str
    previous_response_id: str
    provider_id: str
    model_name: str
    updated_at: str


class ResponseStateRepositoryError(RuntimeError):
    """Raised when response_state repository operations fail."""


class ResponseStateRepository:
    def __init__(self, path_manager: StoragePathManager) -> None:
        self.path_manager = path_manager

    def get_previous_response_id(self, scope_id: str, topic_id: str) -> str | None:
        record = self.get_state(scope_id=scope_id, topic_id=topic_id)
        if record is None:
            return None
        return record.previous_response_id

    def get_state(self, scope_id: str, topic_id: str) -> ResponseStateRecord | None:
        scope_id, topic_id = _normalize_scope_topic(scope_id=scope_id, topic_id=topic_id)
        try:
            with sqlite3.connect(self.path_manager.response_state_db_path()) as conn:
                _ensure_response_state_table(conn)
                row = conn.execute(
                    """
                    SELECT scope_id, topic_id, previous_response_id, provider_id, model_name, updated_at
                    FROM response_state
                    WHERE scope_id = ? AND topic_id = ?
                    """,
                    (scope_id, topic_id),
                ).fetchone()
        except sqlite3.Error as exc:
            raise ResponseStateRepositoryError("response_state_read_failed") from exc

        if row is None:
            return None
        return ResponseStateRecord(
            scope_id=str(row[0]),
            topic_id=str(row[1]),
            previous_response_id=str(row[2]),
            provider_id=str(row[3]),
            model_name=str(row[4]),
            updated_at=str(row[5]),
        )

    def upsert_state(
        self,
        scope_id: str,
        topic_id: str,
        previous_response_id: str,
        provider_id: str = "",
        model_name: str = "",
        updated_at: str | None = None,
    ) -> None:
        scope_id, topic_id = _normalize_scope_topic(scope_id=scope_id, topic_id=topic_id)
        previous_response_id = _normalize_required_text(previous_response_id, field_name="previous_response_id")
        updated_at_value = _normalize_optional_text(updated_at) or datetime.now(timezone.utc).isoformat()
        provider_id_value = _normalize_optional_text(provider_id)
        model_name_value = _normalize_optional_text(model_name)

        try:
            with sqlite3.connect(self.path_manager.response_state_db_path()) as conn:
                _ensure_response_state_table(conn)
                conn.execute(
                    """
                    INSERT INTO response_state(
                        scope_id, topic_id, previous_response_id, provider_id, model_name, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(scope_id, topic_id) DO UPDATE SET
                        previous_response_id = excluded.previous_response_id,
                        provider_id = excluded.provider_id,
                        model_name = excluded.model_name,
                        updated_at = excluded.updated_at
                    """,
                    (
                        scope_id,
                        topic_id,
                        previous_response_id,
                        provider_id_value,
                        model_name_value,
                        updated_at_value,
                    ),
                )
                conn.commit()
        except sqlite3.Error as exc:
            raise ResponseStateRepositoryError("response_state_upsert_failed") from exc

    def delete_state(self, scope_id: str, topic_id: str) -> int:
        return self.delete_by_scope_topic(scope_id=scope_id, topic_id=topic_id)

    def delete_by_scope_topic(self, scope_id: str, topic_id: str) -> int:
        scope_id, topic_id = _normalize_scope_topic(scope_id=scope_id, topic_id=topic_id)
        try:
            with sqlite3.connect(self.path_manager.response_state_db_path()) as conn:
                _ensure_response_state_table(conn)
                before_changes = conn.total_changes
                conn.execute(
                    """
                    DELETE FROM response_state
                    WHERE scope_id = ? AND topic_id = ?
                    """,
                    (scope_id, topic_id),
                )
                conn.commit()
                return conn.total_changes - before_changes
        except sqlite3.Error as exc:
            raise ResponseStateRepositoryError("response_state_delete_failed") from exc


def _normalize_scope_topic(scope_id: str, topic_id: str) -> tuple[str, str]:
    scope_value = _normalize_required_text(scope_id, field_name="scope_id")
    topic_value = _normalize_required_text(topic_id, field_name="topic_id")
    return scope_value, topic_value


def _normalize_required_text(value: str, field_name: str) -> str:
    normalized = _normalize_optional_text(value)
    if not normalized:
        raise ValueError(f"{field_name}_required")
    return normalized


def _normalize_optional_text(value: str | None) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _ensure_response_state_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS response_state (
            scope_id TEXT NOT NULL,
            topic_id TEXT NOT NULL,
            previous_response_id TEXT NOT NULL,
            provider_id TEXT NOT NULL DEFAULT '',
            model_name TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL,
            PRIMARY KEY (scope_id, topic_id)
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_response_state_updated_at
        ON response_state(updated_at)
        """
    )

