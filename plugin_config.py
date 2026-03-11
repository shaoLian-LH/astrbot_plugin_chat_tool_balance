from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import logging
from typing import Any

try:
    from astrbot.api import logger
except ModuleNotFoundError:  # pragma: no cover - fallback for local unit tests.
    logger = logging.getLogger(__name__)

from .storage.path_manager import DEFAULT_BASE_DIR, DEFAULT_BUCKET_COUNT

DEFAULT_NON_BOT_TRIGGER = 20
DEFAULT_SILENCE_TRIGGER_MINUTES = 60


@dataclass(frozen=True)
class ModelSettings:
    chat_default: str = ""
    ocr: str = ""
    topic_classifier: str = ""
    tool_intent_classifier: str = ""
    summary: str = ""
    chat_model: str = ""
    ocr_model: str = ""
    topic_classifier_model: str = ""
    tool_intent_classifier_model: str = ""
    summary_model: str = ""


@dataclass(frozen=True)
class SummaryTriggerSettings:
    trigger_non_bot_count: int = DEFAULT_NON_BOT_TRIGGER
    trigger_silence_minutes: int = DEFAULT_SILENCE_TRIGGER_MINUTES


@dataclass(frozen=True)
class SummarySettings:
    trigger: SummaryTriggerSettings
    enabled: bool = True


@dataclass(frozen=True)
class FeatureSettings:
    use_responses_api: bool = True


@dataclass(frozen=True)
class StorageSettings:
    base_dir: str = DEFAULT_BASE_DIR
    bucket_count: int = DEFAULT_BUCKET_COUNT


@dataclass(frozen=True)
class PluginSettings:
    models: ModelSettings
    features: FeatureSettings
    summary: SummarySettings
    storage: StorageSettings


def load_plugin_settings(raw_config: Mapping[str, Any]) -> PluginSettings:
    models_raw = _as_dict(raw_config.get("models"))
    chat_default = _as_string(models_raw.get("chat_default"), "")
    chat_model = _as_string(models_raw.get("chat_model"), "")
    model_settings = ModelSettings(
        chat_default=chat_default,
        ocr=_model_or_default(models_raw.get("ocr"), chat_default),
        topic_classifier=_model_or_default(models_raw.get("topic_classifier"), chat_default),
        tool_intent_classifier=_model_or_default(
            models_raw.get("tool_intent_classifier"), chat_default
        ),
        summary=_model_or_default(models_raw.get("summary"), chat_default),
        chat_model=chat_model,
        ocr_model=_model_or_default(models_raw.get("ocr_model"), chat_model),
        topic_classifier_model=_model_or_default(
            models_raw.get("topic_classifier_model"),
            chat_model,
        ),
        tool_intent_classifier_model=_model_or_default(
            models_raw.get("tool_intent_classifier_model"),
            chat_model,
        ),
        summary_model=_model_or_default(models_raw.get("summary_model"), chat_model),
    )

    summary_raw = _as_dict(raw_config.get("summary"))
    trigger_settings = SummaryTriggerSettings(
        trigger_non_bot_count=_as_positive_int(
            summary_raw.get("trigger_non_bot_count"),
            DEFAULT_NON_BOT_TRIGGER,
            "summary.trigger_non_bot_count",
        ),
        trigger_silence_minutes=_as_positive_int(
            summary_raw.get("trigger_silence_minutes"),
            DEFAULT_SILENCE_TRIGGER_MINUTES,
            "summary.trigger_silence_minutes",
        ),
    )
    summary_enabled = _as_bool(summary_raw.get("enabled"), True, "summary.enabled")

    features_raw = _as_dict(raw_config.get("features"))
    feature_settings = FeatureSettings(
        use_responses_api=_as_bool(
            features_raw.get("use_responses_api"),
            True,
            "features.use_responses_api",
        )
    )

    storage_raw = _as_dict(raw_config.get("storage"))
    base_dir = _storage_base_dir(storage_raw.get("base_dir"))
    bucket_count = _bucket_count(storage_raw.get("bucket_count"))
    storage_settings = StorageSettings(base_dir=base_dir, bucket_count=bucket_count)

    return PluginSettings(
        models=model_settings,
        features=feature_settings,
        summary=SummarySettings(enabled=summary_enabled, trigger=trigger_settings),
        storage=storage_settings,
    )


def _as_dict(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _as_string(value: Any, default: str) -> str:
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip()
    logger.warning("配置值类型错误，回退默认值: %s", value)
    return default


def _model_or_default(value: Any, chat_default: str) -> str:
    model_name = _as_string(value, "")
    return model_name or chat_default


def _as_positive_int(value: Any, default: int, field_name: str) -> int:
    if value is None:
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        logger.warning("配置字段 %s 不是整数，回退默认值 %s", field_name, default)
        return default
    if parsed <= 0:
        logger.warning("配置字段 %s 必须大于 0，回退默认值 %s", field_name, default)
        return default
    return parsed


def _as_bool(value: Any, default: bool, field_name: str) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "on"}:
            return True
        if lowered in {"false", "0", "no", "off"}:
            return False
    logger.warning("配置字段 %s 不是布尔值，回退默认值 %s", field_name, default)
    return default


def _storage_base_dir(value: Any) -> str:
    base_dir = _as_string(value, DEFAULT_BASE_DIR)
    if not base_dir.startswith("/"):
        logger.warning(
            "配置字段 storage.base_dir 需要绝对路径，已回退默认值 %s",
            DEFAULT_BASE_DIR,
        )
        return DEFAULT_BASE_DIR
    return base_dir


def _bucket_count(value: Any) -> int:
    if value is None:
        return DEFAULT_BUCKET_COUNT
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        logger.warning(
            "配置字段 storage.bucket_count 不是整数，回退默认值 %s",
            DEFAULT_BUCKET_COUNT,
        )
        return DEFAULT_BUCKET_COUNT
    if parsed != DEFAULT_BUCKET_COUNT:
        logger.warning(
            "配置字段 storage.bucket_count 已固定为 %s，忽略配置值 %s",
            DEFAULT_BUCKET_COUNT,
            parsed,
        )
    return DEFAULT_BUCKET_COUNT
