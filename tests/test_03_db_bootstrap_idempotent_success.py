import sqlite3

from storage.bootstrap import initialize_storage


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


def test_db_bootstrap_create_schema_success(tmp_path):
    base_dir = str(tmp_path / "plugin_data")
    result = initialize_storage(base_dir=base_dir, bucket_count=10)
    path_manager = result.path_manager

    assert len(result.db_paths) == 22
    assert path_manager.core_db_path().exists()
    assert path_manager.summary_jobs_db_path().exists()

    with sqlite3.connect(path_manager.core_db_path()) as conn:
        assert _table_exists(conn, "schema_version")
        assert _table_exists(conn, "sessions")
        assert _table_exists(conn, "topics")
        assert _table_exists(conn, "session_topic_map")
        assert _table_exists(conn, "topic_activity")
        assert _table_exists(conn, "tool_intent_log")
        assert _table_exists(conn, "config_snapshot")
        assert _primary_key_columns(conn, "topics") == ("scope_id", "topic_id")

    with sqlite3.connect(path_manager.summary_jobs_db_path()) as conn:
        assert _table_exists(conn, "schema_version")
        assert _table_exists(conn, "summary_jobs")
        assert _table_exists(conn, "summary_results")
        assert _table_exists(conn, "livingmemory_sync_log")

    with sqlite3.connect(path_manager.short_memory_bucket_path(0)) as conn:
        assert _table_exists(conn, "schema_version")
        assert _table_exists(conn, "messages")
        assert _table_exists(conn, "topic_message_map")
        assert _table_exists(conn, "short_summary_cursor")
        assert _table_exists(conn, "message_embeddings")
        assert _table_exists(conn, "message_embeddings_vec") is False

    with sqlite3.connect(path_manager.image_cache_bucket_path(0)) as conn:
        assert _table_exists(conn, "schema_version")
        assert _table_exists(conn, "image_descriptions")
        assert _table_exists(conn, "image_access_log")


def test_db_bootstrap_idempotent_success(tmp_path):
    base_dir = str(tmp_path / "plugin_data")
    first = initialize_storage(base_dir=base_dir, bucket_count=10)
    core_db = first.path_manager.core_db_path()

    with sqlite3.connect(core_db) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO sessions(session_id, scope) VALUES (?, ?)",
            ("session-a", "group"),
        )
        conn.commit()

    second = initialize_storage(base_dir=base_dir, bucket_count=10)
    assert second.path_manager.core_db_path() == core_db

    with sqlite3.connect(core_db) as conn:
        row = conn.execute(
            "SELECT scope FROM sessions WHERE session_id = ?",
            ("session-a",),
        ).fetchone()
        assert row is not None
        assert row[0] == "group"


def test_db_bootstrap_topics_scope_pk_migration_success(tmp_path):
    base_dir = str(tmp_path / "plugin_data")
    result = initialize_storage(base_dir=base_dir, bucket_count=10)
    core_db = result.path_manager.core_db_path()

    with sqlite3.connect(core_db) as conn:
        conn.execute("DROP TABLE topics")
        conn.execute(
            """
            CREATE TABLE topics (
                topic_id TEXT PRIMARY KEY,
                scope_id TEXT NOT NULL,
                title TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            "INSERT INTO topics(topic_id, scope_id, title) VALUES (?, ?, ?)",
            ("weather", "scope-a", "weather"),
        )
        conn.commit()

    initialize_storage(base_dir=base_dir, bucket_count=10)

    with sqlite3.connect(core_db) as conn:
        assert _primary_key_columns(conn, "topics") == ("scope_id", "topic_id")
        row = conn.execute(
            "SELECT title FROM topics WHERE scope_id = ? AND topic_id = ?",
            ("scope-a", "weather"),
        ).fetchone()
        assert row is not None
        assert row[0] == "weather"


def test_db_bootstrap_message_embeddings_migration_success(tmp_path):
    base_dir = str(tmp_path / "plugin_data")
    result = initialize_storage(base_dir=base_dir, bucket_count=10)
    short_db = result.path_manager.short_memory_bucket_path(0)

    with sqlite3.connect(short_db) as conn:
        conn.execute("DROP TABLE message_embeddings")
        conn.execute(
            """
            CREATE TABLE message_embeddings (
                embedding_id INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id TEXT NOT NULL,
                embedding BLOB
            )
            """
        )
        conn.execute(
            """
            INSERT INTO messages(message_id, scope_id, topic_id, role, content)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("m-legacy-1", "scope-a", "topic-a", "user", "legacy message"),
        )
        conn.execute(
            "INSERT INTO message_embeddings(message_id, embedding) VALUES (?, ?)",
            ("m-legacy-1", b"legacy"),
        )
        conn.commit()

    initialize_storage(base_dir=base_dir, bucket_count=10)

    with sqlite3.connect(short_db) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(message_embeddings)").fetchall()}
        assert {"message_id", "scope_id", "topic_id", "embedding_json", "backend", "updated_at"} <= columns
        row = conn.execute(
            """
            SELECT scope_id, topic_id, backend
            FROM message_embeddings
            WHERE message_id = ?
            """,
            ("m-legacy-1",),
        ).fetchone()
        assert row is not None
        assert row[0] == "scope-a"
        assert row[1] == "topic-a"
        assert row[2] == "legacy_fallback"
