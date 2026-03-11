from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol

from ..bridge.livingmemory_v2_bridge import LivingMemoryV2Bridge
from ..pipeline.contracts import ShortMemoryRecord
from ..storage.path_manager import StoragePathManager

SummaryGenerator = Callable[[tuple[ShortMemoryRecord, ...], str], tuple[str, float]]


class SummaryStateJanitorProtocol(Protocol):
    def delete_by_scope_topic(self, scope_id: str, topic_id: str) -> int:
        ...


@dataclass(frozen=True)
class SummaryExecutionResult:
    job_id: int
    result_id: int | None
    status: str
    pending_sync: bool
    error: str = ""


class SummaryExecutor:
    """Execute summary jobs and keep local->LM sync state consistent."""

    def __init__(
        self,
        path_manager: StoragePathManager,
        summary_model_name: str = "",
        summary_generator: SummaryGenerator | None = None,
        bridge: LivingMemoryV2Bridge | None = None,
        summary_state_janitor: SummaryStateJanitorProtocol | None = None,
        max_source_messages: int = 20,
        base_retry_seconds: int = 10,
    ) -> None:
        self.path_manager = path_manager
        self.summary_model_name = summary_model_name
        self.summary_generator = summary_generator or _default_generate_summary
        self.bridge = bridge
        self.summary_state_janitor = summary_state_janitor
        self.max_source_messages = max(4, int(max_source_messages))
        self.base_retry_seconds = max(1, int(base_retry_seconds))

    def execute_job(self, job_id: int, now: datetime | None = None) -> SummaryExecutionResult | None:
        now_dt = _as_utc_datetime(now)
        now_iso = now_dt.isoformat()

        with sqlite3.connect(self.path_manager.summary_jobs_db_path()) as conn:
            self._ensure_summary_tables(conn)
            job_row = conn.execute(
                """
                SELECT id, scope_id, topic_id, status, retry_count
                FROM summary_jobs
                WHERE id = ?
                """,
                (int(job_id),),
            ).fetchone()
            if job_row is None:
                return None
            if str(job_row[3]) == "running":
                return None
            conn.execute(
                """
                UPDATE summary_jobs
                SET status = 'running', updated_at = ?, error_text = NULL
                WHERE id = ?
                """,
                (now_iso, int(job_id)),
            )
            conn.commit()

        scope_id = str(job_row[1])
        topic_id = str(job_row[2])
        retry_count = int(job_row[4]) if job_row[4] is not None else 0

        messages = self._load_recent_topic_messages(scope_id=scope_id, topic_id=topic_id)
        source_window = _build_source_window(messages)

        try:
            summary_text, quality = self.summary_generator(messages, self.summary_model_name)
        except Exception as exc:
            next_retry_at = self._next_retry_at(now_dt, retry_count + 1)
            with sqlite3.connect(self.path_manager.summary_jobs_db_path()) as conn:
                self._ensure_summary_tables(conn)
                conn.execute(
                    """
                    UPDATE summary_jobs
                    SET status = 'failed',
                        retry_count = ?,
                        next_retry_at = ?,
                        error_text = ?,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (retry_count + 1, next_retry_at, str(exc), now_iso, int(job_id)),
                )
                conn.commit()
            return SummaryExecutionResult(
                job_id=int(job_id),
                result_id=None,
                status="failed",
                pending_sync=False,
                error=str(exc),
            )

        quality_value = _clamp_quality(quality)
        source_window_json = json.dumps(source_window, ensure_ascii=False, sort_keys=True)
        result_id = self._upsert_summary_result(
            job_id=int(job_id),
            summary_text=summary_text,
            source_window=source_window_json,
            quality=quality_value,
            pending_sync=0,
            now_iso=now_iso,
            last_sync_error=None,
        )

        sync_ok, sync_error = self._sync_result_once(
            scope_id=scope_id,
            topic_id=topic_id,
            summary_text=summary_text,
            source_window=source_window,
        )
        if sync_ok:
            with sqlite3.connect(self.path_manager.summary_jobs_db_path()) as conn:
                self._ensure_summary_tables(conn)
                conn.execute(
                    """
                    UPDATE summary_results
                    SET pending_sync = 0,
                        synced_at = ?,
                        last_sync_error = NULL
                    WHERE id = ?
                    """,
                    (now_iso, int(result_id)),
                )
                conn.execute(
                    """
                    UPDATE summary_jobs
                    SET status = 'completed',
                        next_retry_at = NULL,
                        error_text = NULL,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (now_iso, int(job_id)),
                )
                self._append_sync_log(
                    conn=conn,
                    result_id=int(result_id),
                    status="success",
                    detail="initial_sync_ok",
                    now_iso=now_iso,
                )
                conn.commit()
            self._cleanup_response_state(scope_id=scope_id, topic_id=topic_id)
            return SummaryExecutionResult(
                job_id=int(job_id),
                result_id=int(result_id),
                status="completed",
                pending_sync=False,
            )

        next_retry_at = self._next_retry_at(now_dt, retry_count + 1)
        with sqlite3.connect(self.path_manager.summary_jobs_db_path()) as conn:
            self._ensure_summary_tables(conn)
            conn.execute(
                """
                UPDATE summary_results
                SET pending_sync = 1,
                    synced_at = NULL,
                    last_sync_error = ?
                WHERE id = ?
                """,
                (sync_error, int(result_id)),
            )
            conn.execute(
                """
                UPDATE summary_jobs
                SET status = 'sync_pending',
                    retry_count = ?,
                    next_retry_at = ?,
                    error_text = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (retry_count + 1, next_retry_at, sync_error, now_iso, int(job_id)),
            )
            self._append_sync_log(
                conn=conn,
                result_id=int(result_id),
                status="pending",
                detail=sync_error,
                now_iso=now_iso,
            )
            conn.commit()
        return SummaryExecutionResult(
            job_id=int(job_id),
            result_id=int(result_id),
            status="sync_pending",
            pending_sync=True,
            error=sync_error,
        )

    def retry_failed_jobs(self, now: datetime | None = None, limit: int = 20) -> tuple[SummaryExecutionResult, ...]:
        now_dt = _as_utc_datetime(now)
        now_iso = now_dt.isoformat()
        with sqlite3.connect(self.path_manager.summary_jobs_db_path()) as conn:
            self._ensure_summary_tables(conn)
            rows = conn.execute(
                """
                SELECT id
                FROM summary_jobs
                WHERE status = 'failed'
                  AND (next_retry_at IS NULL OR next_retry_at <= ?)
                ORDER BY updated_at ASC, id ASC
                LIMIT ?
                """,
                (now_iso, max(1, int(limit))),
            ).fetchall()

        results: list[SummaryExecutionResult] = []
        for row in rows:
            executed = self.execute_job(int(row[0]), now=now_dt)
            if executed is not None:
                results.append(executed)
        return tuple(results)

    def retry_pending_sync(self, now: datetime | None = None, limit: int = 20) -> int:
        if self.bridge is None:
            return 0

        now_dt = _as_utc_datetime(now)
        now_iso = now_dt.isoformat()
        synced_count = 0

        with sqlite3.connect(self.path_manager.summary_jobs_db_path()) as conn:
            self._ensure_summary_tables(conn)
            rows = conn.execute(
                """
                SELECT
                    sr.id,
                    sr.job_id,
                    sr.summary_text,
                    sr.source_window,
                    sj.scope_id,
                    sj.topic_id,
                    sj.retry_count
                FROM summary_results AS sr
                JOIN summary_jobs AS sj ON sj.id = sr.job_id
                WHERE sr.pending_sync = 1
                  AND (sj.next_retry_at IS NULL OR sj.next_retry_at <= ?)
                ORDER BY sr.id ASC
                LIMIT ?
                """,
                (now_iso, max(1, int(limit))),
            ).fetchall()

        for result_id, job_id, summary_text, source_window, scope_id, topic_id, retry_count in rows:
            try:
                source_window_obj = json.loads(source_window) if source_window else {}
            except json.JSONDecodeError:
                source_window_obj = {}
            sync_ok, sync_error = self._sync_result_once(
                scope_id=str(scope_id),
                topic_id=str(topic_id),
                summary_text=str(summary_text),
                source_window=source_window_obj if isinstance(source_window_obj, dict) else {},
            )
            cleanup_after_commit = False
            with sqlite3.connect(self.path_manager.summary_jobs_db_path()) as conn:
                self._ensure_summary_tables(conn)
                if sync_ok:
                    conn.execute(
                        """
                        UPDATE summary_results
                        SET pending_sync = 0,
                            synced_at = ?,
                            last_sync_error = NULL
                        WHERE id = ?
                        """,
                        (now_iso, int(result_id)),
                    )
                    conn.execute(
                        """
                        UPDATE summary_jobs
                        SET status = 'completed',
                            next_retry_at = NULL,
                            error_text = NULL,
                            updated_at = ?
                        WHERE id = ?
                        """,
                        (now_iso, int(job_id)),
                    )
                    self._append_sync_log(
                        conn=conn,
                        result_id=int(result_id),
                        status="success",
                        detail="retry_sync_ok",
                        now_iso=now_iso,
                    )
                    synced_count += 1
                    cleanup_after_commit = True
                else:
                    retry_value = int(retry_count) + 1
                    next_retry_at = self._next_retry_at(now_dt, retry_value)
                    conn.execute(
                        """
                        UPDATE summary_results
                        SET pending_sync = 1,
                            sync_retry_count = sync_retry_count + 1,
                            last_sync_error = ?
                        WHERE id = ?
                        """,
                        (sync_error, int(result_id)),
                    )
                    conn.execute(
                        """
                        UPDATE summary_jobs
                        SET status = 'sync_pending',
                            retry_count = ?,
                            next_retry_at = ?,
                            error_text = ?,
                            updated_at = ?
                        WHERE id = ?
                        """,
                        (retry_value, next_retry_at, sync_error, now_iso, int(job_id)),
                    )
                    self._append_sync_log(
                        conn=conn,
                        result_id=int(result_id),
                        status="pending",
                        detail=sync_error,
                        now_iso=now_iso,
                    )
                conn.commit()
            if cleanup_after_commit:
                self._cleanup_response_state(
                    scope_id=str(scope_id),
                    topic_id=str(topic_id),
                )

        return synced_count

    def _upsert_summary_result(
        self,
        job_id: int,
        summary_text: str,
        source_window: str,
        quality: float,
        pending_sync: int,
        now_iso: str,
        last_sync_error: str | None,
    ) -> int:
        with sqlite3.connect(self.path_manager.summary_jobs_db_path()) as conn:
            self._ensure_summary_tables(conn)
            row = conn.execute(
                "SELECT id FROM summary_results WHERE job_id = ? ORDER BY id DESC LIMIT 1",
                (int(job_id),),
            ).fetchone()
            if row is None:
                cursor = conn.execute(
                    """
                    INSERT INTO summary_results(
                        job_id, summary_text, source_window, quality, pending_sync, last_sync_error, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        int(job_id),
                        summary_text,
                        source_window,
                        float(quality),
                        int(pending_sync),
                        last_sync_error,
                        now_iso,
                    ),
                )
                result_id = int(cursor.lastrowid)
            else:
                result_id = int(row[0])
                conn.execute(
                    """
                    UPDATE summary_results
                    SET summary_text = ?,
                        source_window = ?,
                        quality = ?,
                        pending_sync = ?,
                        last_sync_error = ?,
                        synced_at = CASE WHEN ? = 0 THEN ? ELSE NULL END
                    WHERE id = ?
                    """,
                    (
                        summary_text,
                        source_window,
                        float(quality),
                        int(pending_sync),
                        last_sync_error,
                        int(pending_sync),
                        now_iso,
                        result_id,
                    ),
                )
            conn.commit()
            return result_id

    def _load_recent_topic_messages(self, scope_id: str, topic_id: str) -> tuple[ShortMemoryRecord, ...]:
        db_path = self.path_manager.short_memory_bucket_by_key(f"{scope_id}:{topic_id}")
        with sqlite3.connect(db_path) as conn:
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
            rows = conn.execute(
                """
                SELECT message_id, role, content, created_at
                FROM messages
                WHERE scope_id = ? AND topic_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (scope_id, topic_id, int(self.max_source_messages)),
            ).fetchall()

        records = [
            ShortMemoryRecord(
                message_id=str(row[0]),
                scope_id=scope_id,
                topic_id=topic_id,
                role=str(row[1]),
                content=str(row[2]),
                created_at=str(row[3]),
            )
            for row in reversed(rows)
        ]
        return tuple(records)

    def _sync_result_once(
        self,
        scope_id: str,
        topic_id: str,
        summary_text: str,
        source_window: dict[str, object],
    ) -> tuple[bool, str]:
        if self.bridge is None:
            return False, "bridge_not_configured"
        result = self.bridge.sync_summary_with_retry(
            scope_id=scope_id,
            topic_id=topic_id,
            summary_text=summary_text,
            metadata={"source_window": source_window},
            max_attempts=1,
            base_delay_seconds=0,
            sleep_fn=lambda _seconds: None,
        )
        if result.success:
            return True, ""
        return False, result.error or "sync_failed"

    def _next_retry_at(self, now: datetime, retry_count: int) -> str:
        delay_seconds = self.base_retry_seconds * (2 ** max(0, int(retry_count) - 1))
        return (now + timedelta(seconds=delay_seconds)).isoformat()

    def _cleanup_response_state(self, scope_id: str, topic_id: str) -> None:
        if self.summary_state_janitor is None:
            return
        try:
            self.summary_state_janitor.delete_by_scope_topic(
                scope_id=scope_id,
                topic_id=topic_id,
            )
        except Exception:
            return

    @staticmethod
    def _append_sync_log(
        conn: sqlite3.Connection,
        result_id: int,
        status: str,
        detail: str,
        now_iso: str,
    ) -> None:
        conn.execute(
            """
            INSERT INTO livingmemory_sync_log(result_id, status, detail, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (int(result_id), status, detail, now_iso),
        )

    @staticmethod
    def _ensure_summary_tables(conn: sqlite3.Connection) -> None:
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


def _default_generate_summary(
    records: tuple[ShortMemoryRecord, ...],
    _model_name: str,
) -> tuple[str, float]:
    if not records:
        return "暂无可总结内容。", 0.2
    lines = [f"{item.role}: {item.content}" for item in records[-6:]]
    summary = "；".join(lines)
    quality = min(0.95, 0.55 + 0.03 * len(records))
    return summary, quality


def _build_source_window(records: tuple[ShortMemoryRecord, ...]) -> dict[str, object]:
    if not records:
        return {
            "message_count": 0,
            "first_message_id": "",
            "last_message_id": "",
            "start_at": "",
            "end_at": "",
        }
    return {
        "message_count": len(records),
        "first_message_id": records[0].message_id,
        "last_message_id": records[-1].message_id,
        "start_at": records[0].created_at,
        "end_at": records[-1].created_at,
    }


def _as_utc_datetime(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    if now.tzinfo is None:
        return now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc)


def _clamp_quality(value: float) -> float:
    return max(0.0, min(1.0, float(value)))
