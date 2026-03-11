from __future__ import annotations

from typing import Any

__all__ = [
    "SummaryExecutionResult",
    "SummaryExecutor",
    "SummaryJobRecord",
    "SummaryScheduler",
    "SummaryStateJanitor",
]


def __getattr__(name: str) -> Any:
    if name not in __all__:
        raise AttributeError(name)
    if name in {"SummaryExecutionResult", "SummaryExecutor"}:
        from scheduler.summary_executor import SummaryExecutionResult, SummaryExecutor

        exports = {
            "SummaryExecutionResult": SummaryExecutionResult,
            "SummaryExecutor": SummaryExecutor,
        }
        return exports[name]
    if name in {"SummaryJobRecord", "SummaryScheduler"}:
        from scheduler.summary_scheduler import SummaryJobRecord, SummaryScheduler

        exports = {
            "SummaryJobRecord": SummaryJobRecord,
            "SummaryScheduler": SummaryScheduler,
        }
        return exports[name]

    from scheduler.summary_state_janitor import SummaryStateJanitor

    return SummaryStateJanitor
