from __future__ import annotations

from typing import Any

__all__ = ["RuntimeWiringResult", "build_runtime_wiring"]


def __getattr__(name: str) -> Any:
    if name not in {"RuntimeWiringResult", "build_runtime_wiring"}:
        raise AttributeError(name)
    from services.runtime_wiring import RuntimeWiringResult, build_runtime_wiring

    exports = {
        "RuntimeWiringResult": RuntimeWiringResult,
        "build_runtime_wiring": build_runtime_wiring,
    }
    return exports[name]
