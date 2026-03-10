from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from pipeline.contracts import NormalizedEvent, TopicAssignment
from storage.path_manager import StoragePathManager


@dataclass(frozen=True)
class SummaryJobRecord:
    id: int
    scope_id: str
    topic_id: str
    trigger_type: str
    status: str
    dedupe_key: str | None = None


class SummaryScheduler:
    """Counter + silence based summary trigger scheduler."""

    def __init__(
        self,
        path_manager: StoragePathManager,
        trigger_non_bot_count: int = 20,
        trigger_silence_minutes: int = 60,
    ) -> None:
        self.path_manager = path_manager
        self.trigger_non_bot_count = max(1, int(trigger_non_bot_count))
        self.trigger_silence_minutes = max(1, int(trigger_silence_minutes))

    def record_topic_activity(
        self,
        event: NormalizedEvent,
        topic: TopicAssignment,
        now: datetime | None = None,
    ) -> tuple[SummaryJobRecord, ...]:
        now_dt = _as_utc_datetime(now, event.created_at)
        now_iso = now_dt.isoformat()
        non_bot_count = self._upsert_topic_activity(
            scope_id=topic.scope_id,
            topic_id=topic.topic_id,
            is_bot=event.is_bot,
            now_iso=now_iso,
        )
        if event.is_bot:
            return ()
        if non_bot_count < self.trigger_non_bot_count:
            return ()
        if non_bot_count % self.trigger_non_bot_count != 0:
            return ()

        window_idx = non_bot_count // self.trigger_non_bot_count
        dedupe_key = f"counter:{topic.scope_id}:{topic.topic_id}:{window_idx}"
        job = self._create_summary_job(
            scope_id=topic.scope_id,
            topic_id=topic.topic_id,
            trigger_type="counter_trigger",
            dedupe_key=dedupe_key,
            now_iso=now_iso,
        )
        return (job,) if job is not None else ()

    def poll_silence(
        self,
        now: datetime | None = None,
        limit: int = 256,
    ) -> tuple[SummaryJobRecord, ...]:
        now_dt = _as_utc_datetime(now)
        now_iso = now_dt.isoformat()
        silence_delta = timedelta(minutes=self.trigger_silence_minutes)
        created_jobs: list[SummaryJobRecord] = []

        with sqlite3.connect(self.path_manager.core_db_path()) as conn:
            self._ensure_topic_activity_table(conn)
            rows = conn.execute(
                """
                SELECT scope_id, topic_id, last_message_at
                FROM topic_activity
                WHERE non_bot_message_count > 0
                  AND last_message_at IS NOT NULL
                ORDER BY last_message_at ASC
                LIMIT ?
                """,
                (max(1, int(limit)),),
            ).fetchall()

        for scope_id, topic_id, last_message_at in rows:
            last_dt = _as_utc_datetime(None, str(last_message_at))
            if now_dt - last_dt < silence_delta:
                continue
            dedupe_key = f"silence:{scope_id}:{topic_id}:{last_message_at}"
            job = self._create_summary_job(
                scope_id=scope_id,
                topic_id=topic_id,
                trigger_type="silence_trigger",
                dedupe_key=dedupe_key,
                now_iso=now_iso,
            )
            if job is not None:
                created_jobs.append(job)
        return tuple(created_jobs)

    def _upsert_topic_activity(
        self,
        scope_id: str,
        topic_id: str,
        is_bot: bool,
        now_iso: str,
    ) -> int:
        with sqlite3.connect(self.path_manager.core_db_path()) as conn:
            self._ensure_topic_activity_table(conn)
            row = conn.execute(
                """
                SELECT non_bot_message_count
                FROM topic_activity
                WHERE scope_id = ? AND topic_id = ?
                """,
                (scope_id, topic_id),
            ).fetchone()
            current = int(row[0]) if row is not None else 0
            next_count = current if is_bot else current + 1
            conn.execute(
                """
                INSERT INTO topic_activity(scope_id, topic_id, non_bot_message_count, last_message_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(scope_id, topic_id) DO UPDATE SET
                    non_bot_message_count = excluded.non_bot_message_count,
                    last_message_at = excluded.last_message_at
                """,
                (scope_id, topic_id, next_count, now_iso),
            )
            conn.commit()
            return next_count

    def _create_summary_job(
        self,
        scope_id: str,
        topic_id: str,
        trigger_type: str,
        dedupe_key: str | None,
        now_iso: str,
    ) -> SummaryJobRecord | None:
        with sqlite3.connect(self.path_manager.summary_jobs_db_path()) as conn:
            self._ensure_summary_jobs_table(conn)
            conn.execute(
                """
                INSERT INTO summary_jobs(
                    scope_id, topic_id, trigger_type, dedupe_key, status, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, 'pending', ?, ?)
                ON CONFLICT(dedupe_key) DO NOTHING
                """,
                (scope_id, topic_id, trigger_type, dedupe_key, now_iso, now_iso),
            )
            if conn.total_changes <= 0:
                return None
            row = conn.execute(
                """
                SELECT id, scope_id, topic_id, trigger_type, status, dedupe_key
                FROM summary_jobs
                WHERE id = last_insert_rowid()
                """
            ).fetchone()
            conn.commit()

        if row is None:
            return None
        return SummaryJobRecord(
            id=int(row[0]),
            scope_id=str(row[1]),
            topic_id=str(row[2]),
            trigger_type=str(row[3]),
            status=str(row[4]),
            dedupe_key=str(row[5]) if row[5] is not None else None,
        )

    @staticmethod
    def _ensure_topic_activity_table(conn: sqlite3.Connection) -> None:
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

    @staticmethod
    def _ensure_summary_jobs_table(conn: sqlite3.Connection) -> None:
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


def _as_utc_datetime(now: datetime | None, fallback_iso: str | None = None) -> datetime:
    if now is not None:
        dt = now
    elif fallback_iso:
        dt = _parse_datetime(fallback_iso)
    else:
        dt = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _parse_datetime(raw: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        parsed = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
