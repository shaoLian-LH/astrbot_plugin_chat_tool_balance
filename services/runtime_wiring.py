from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

try:
    from astrbot.api import logger
except ModuleNotFoundError:  # pragma: no cover - fallback for local unit tests.
    logger = logging.getLogger(__name__)

from bridge.livingmemory_v2_bridge import LivingMemoryV2Bridge
from pipeline.orchestrator import ChatToolBalanceOrchestrator
from plugin_config import PluginSettings, load_plugin_settings
from scheduler.summary_executor import SummaryExecutor
from storage.bootstrap import StorageBootstrapResult, initialize_storage


@dataclass(frozen=True)
class RuntimeWiringResult:
    settings: PluginSettings
    storage_bootstrap: StorageBootstrapResult
    livingmemory_bridge: LivingMemoryV2Bridge
    summary_executor: SummaryExecutor
    orchestrator: ChatToolBalanceOrchestrator


def build_runtime_wiring(context: Any, plugin: Any) -> RuntimeWiringResult:
    raw_config = extract_raw_plugin_config(context, plugin)
    settings = load_plugin_settings(raw_config)
    storage_bootstrap = initialize_storage(
        base_dir=settings.storage.base_dir,
        bucket_count=settings.storage.bucket_count,
    )
    path_manager = storage_bootstrap.path_manager
    livingmemory_bridge = LivingMemoryV2Bridge(client_getter=build_livingmemory_client_getter(context))
    summary_executor = SummaryExecutor(
        path_manager=path_manager,
        summary_model_name=settings.models.summary,
        bridge=livingmemory_bridge,
    )
    orchestrator = ChatToolBalanceOrchestrator(
        settings=settings,
        path_manager=path_manager,
        summary_executor=summary_executor,
    )
    return RuntimeWiringResult(
        settings=settings,
        storage_bootstrap=storage_bootstrap,
        livingmemory_bridge=livingmemory_bridge,
        summary_executor=summary_executor,
        orchestrator=orchestrator,
    )


def extract_raw_plugin_config(context: Any, plugin: Any) -> Mapping[str, Any]:
    candidates: list[Any] = [
        getattr(plugin, "config", None),
        getattr(context, "config", None),
    ]
    for candidate in candidates:
        if isinstance(candidate, Mapping):
            return candidate

    for getter_name in ("get_config", "get_plugin_config", "get_plugin_conf"):
        getter = getattr(context, getter_name, None)
        if not callable(getter):
            continue
        for args in ((), ("astrbot_plugin_chat_tool_balance",), ("chat_tool_balance",)):
            try:
                candidate = getter(*args)
            except TypeError:
                continue
            except Exception as exc:  # pragma: no cover - depends on runtime context.
                logger.warning("读取插件配置失败: getter=%s, err=%s", getter_name, exc)
                break
            if isinstance(candidate, Mapping):
                return candidate
    return {}


def build_livingmemory_client_getter(context: Any) -> Callable[[], Any]:
    plugin_keys = (
        "livingmemory_v2",
        "livingmemory",
        "astrbot_plugin_livingmemory_v2",
        "astrbot_plugin_livingmemory",
    )
    getter_specs = (
        ("get_plugin", plugin_keys),
        ("get_plugin_by_name", plugin_keys),
        ("get_star", plugin_keys),
        ("get_service", plugin_keys),
    )

    def _resolve() -> Any:
        for getter_name, names in getter_specs:
            getter = getattr(context, getter_name, None)
            if not callable(getter):
                continue
            for name in names:
                try:
                    candidate = getter(name)
                except Exception:
                    continue
                if candidate is not None:
                    return candidate

        plugin_manager = getattr(context, "plugin_manager", None)
        if plugin_manager is not None:
            for getter_name, names in getter_specs:
                getter = getattr(plugin_manager, getter_name, None)
                if not callable(getter):
                    continue
                for name in names:
                    try:
                        candidate = getter(name)
                    except Exception:
                        continue
                    if candidate is not None:
                        return candidate
        return None

    return _resolve
