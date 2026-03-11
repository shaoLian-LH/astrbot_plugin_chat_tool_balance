from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .path_manager import StoragePathManager

SCHEMA_VERSION = 4


@dataclass(frozen=True)
class StorageBootstrapResult:
    path_manager: StoragePathManager
    db_paths: tuple[Path, ...]


def initialize_storage(base_dir: str, bucket_count: int) -> StorageBootstrapResult:
    path_manager = StoragePathManager(base_dir=base_dir, bucket_count=bucket_count)
    path_manager.ensure_directories()

    db_paths = (
        path_manager.core_db_path(),
        path_manager.response_state_db_path(),
        path_manager.summary_jobs_db_path(),
        *path_manager.short_memory_bucket_paths(),
        *path_manager.image_cache_bucket_paths(),
    )
    for db_path in db_paths:
        _initialize_database(db_path, path_manager)

    return StorageBootstrapResult(path_manager=path_manager, db_paths=db_paths)


def _initialize_database(db_path: Path, path_manager: StoragePathManager) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA foreign_keys = ON;")
        component = _component_name(db_path, path_manager)
        _ensure_schema_version(conn, component, SCHEMA_VERSION)

        if db_path == path_manager.core_db_path():
            _create_core_tables(conn)
        elif db_path == path_manager.response_state_db_path():
            _create_response_state_tables(conn)
        elif db_path == path_manager.summary_jobs_db_path():
            _create_summary_tables(conn)
        elif db_path.parent.name == "short_memory":
            _create_short_memory_tables(conn)
        elif db_path.parent.name == "image":
            _create_image_tables(conn)
        conn.commit()


def _component_name(db_path: Path, path_manager: StoragePathManager) -> str:
    if db_path == path_manager.core_db_path():
        return "core_state"
    if db_path == path_manager.response_state_db_path():
        return "response_state"
    if db_path == path_manager.summary_jobs_db_path():
        return "summary_jobs"
    if db_path.parent.name == "short_memory":
        return "short_memory_bucket"
    if db_path.parent.name == "image":
        return "image_cache_bucket"
    return db_path.stem


def _ensure_schema_version(conn: sqlite3.Connection, component: str, version: int) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_version (
            component TEXT PRIMARY KEY,
            version INTEGER NOT NULL,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        INSERT INTO schema_version (component, version)
        VALUES (?, ?)
        ON CONFLICT(component) DO UPDATE SET
            version = excluded.version,
            updated_at = CURRENT_TIMESTAMP
        WHERE schema_version.version != excluded.version
        """,
        (component, version),
    )


def _create_core_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sessions (
            session_id TEXT PRIMARY KEY,
            scope TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    _ensure_topics_table_scope_pk(conn)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS topic_activity (
            scope_id TEXT NOT NULL,
            topic_id TEXT NOT NULL,
            non_bot_message_count INTEGER NOT NULL DEFAULT 0,
            last_message_at TEXT,
            PRIMARY KEY (scope_id, topic_id)
        )
        """
    )
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
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tool_intent_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id TEXT,
            intent_hit INTEGER NOT NULL DEFAULT 0,
            reason TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS config_snapshot (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )


def _create_summary_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS summary_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scope_id TEXT NOT NULL,
            topic_id TEXT NOT NULL,
            trigger_type TEXT NOT NULL,
            dedupe_key TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            retry_count INTEGER NOT NULL DEFAULT 0,
            next_retry_at TEXT,
            error_text TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_summary_jobs_dedupe_key
        ON summary_jobs(dedupe_key)
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS summary_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id INTEGER NOT NULL,
            summary_text TEXT NOT NULL,
            source_window TEXT NOT NULL DEFAULT '{}',
            quality REAL NOT NULL DEFAULT 0,
            pending_sync INTEGER NOT NULL DEFAULT 0,
            sync_retry_count INTEGER NOT NULL DEFAULT 0,
            synced_at TEXT,
            last_sync_error TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(job_id) REFERENCES summary_jobs(id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS livingmemory_sync_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            result_id INTEGER NOT NULL,
            status TEXT NOT NULL,
            detail TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    _ensure_column(conn, "summary_jobs", "dedupe_key", "TEXT")
    _ensure_column(conn, "summary_jobs", "retry_count", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(conn, "summary_jobs", "next_retry_at", "TEXT")
    _ensure_column(conn, "summary_jobs", "error_text", "TEXT")
    _ensure_column(conn, "summary_results", "source_window", "TEXT NOT NULL DEFAULT '{}'")
    _ensure_column(conn, "summary_results", "quality", "REAL NOT NULL DEFAULT 0")
    _ensure_column(conn, "summary_results", "sync_retry_count", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(conn, "summary_results", "synced_at", "TEXT")
    _ensure_column(conn, "summary_results", "last_sync_error", "TEXT")
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_summary_jobs_dedupe_key
        ON summary_jobs(dedupe_key)
        """
    )


def _create_response_state_tables(conn: sqlite3.Connection) -> None:
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


def _create_short_memory_tables(conn: sqlite3.Connection) -> None:
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
        CREATE TABLE IF NOT EXISTS short_summary_cursor (
            topic_id TEXT PRIMARY KEY,
            last_summary_message_id TEXT,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    _ensure_message_embeddings_table(conn)


def _create_image_tables(conn: sqlite3.Connection) -> None:
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


def _ensure_topics_table_scope_pk(conn: sqlite3.Connection) -> None:
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


def _ensure_message_embeddings_table(conn: sqlite3.Connection) -> None:
    if not _table_exists(conn, "message_embeddings"):
        conn.execute(
            """
            CREATE TABLE message_embeddings (
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
        return

    existing_columns = _table_columns(conn, "message_embeddings")
    expected_columns = {"message_id", "scope_id", "topic_id", "embedding_json", "backend", "updated_at"}
    if expected_columns.issubset(existing_columns):
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_message_embeddings_scope_topic
            ON message_embeddings(scope_id, topic_id)
            """
        )
        return

    conn.execute("ALTER TABLE message_embeddings RENAME TO message_embeddings_legacy")
    conn.execute(
        """
        CREATE TABLE message_embeddings (
            message_id TEXT PRIMARY KEY,
            scope_id TEXT NOT NULL,
            topic_id TEXT NOT NULL,
            embedding_json TEXT NOT NULL,
            backend TEXT NOT NULL DEFAULT 'legacy_fallback',
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        INSERT INTO message_embeddings(message_id, scope_id, topic_id, embedding_json, backend)
        SELECT
            legacy.message_id,
            COALESCE(msg.scope_id, ''),
            COALESCE(msg.topic_id, ''),
            '[]',
            'legacy_fallback'
        FROM (
            SELECT message_id, MAX(embedding_id) AS latest_id
            FROM message_embeddings_legacy
            GROUP BY message_id
        ) AS dedup
        JOIN message_embeddings_legacy AS legacy
          ON legacy.message_id = dedup.message_id AND legacy.embedding_id = dedup.latest_id
        LEFT JOIN messages AS msg
          ON msg.message_id = legacy.message_id
        """
    )
    conn.execute("DROP TABLE message_embeddings_legacy")
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_message_embeddings_scope_topic
        ON message_embeddings(scope_id, topic_id)
        """
    )


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def _table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {row[1] for row in rows}


def _ensure_column(
    conn: sqlite3.Connection,
    table_name: str,
    column_name: str,
    column_decl: str,
) -> None:
    if column_name in _table_columns(conn, table_name):
        return
    conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_decl}")


def _primary_key_columns(conn: sqlite3.Connection, table_name: str) -> tuple[str, ...]:
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    pk_rows = sorted((row for row in rows if int(row[5]) > 0), key=lambda row: int(row[5]))
    return tuple(row[1] for row in pk_rows)
