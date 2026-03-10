from plugin_config import (
    DEFAULT_NON_BOT_TRIGGER,
    DEFAULT_SILENCE_TRIGGER_MINUTES,
    load_plugin_settings,
)
from storage.path_manager import DEFAULT_BASE_DIR, DEFAULT_BUCKET_COUNT


def test_plugin_config_invalid_values_fallback_success():
    settings = load_plugin_settings(
        {
            "models": {
                "chat_default": " chat-base ",
                "ocr": None,
                "topic_classifier": "",
                "tool_intent_classifier": 123,
                "summary": " ",
            },
            "summary": {
                "enabled": "maybe",
                "trigger_non_bot_count": 0,
                "trigger_silence_minutes": "oops",
            },
            "storage": {
                "base_dir": "relative/path",
                "bucket_count": 999,
            },
        }
    )

    assert settings.models.chat_default == "chat-base"
    assert settings.models.ocr == "chat-base"
    assert settings.models.topic_classifier == "chat-base"
    assert settings.models.tool_intent_classifier == "chat-base"
    assert settings.models.summary == "chat-base"

    assert settings.summary.enabled is True
    assert settings.summary.trigger.trigger_non_bot_count == DEFAULT_NON_BOT_TRIGGER
    assert settings.summary.trigger.trigger_silence_minutes == DEFAULT_SILENCE_TRIGGER_MINUTES

    assert settings.storage.base_dir == DEFAULT_BASE_DIR
    assert settings.storage.bucket_count == DEFAULT_BUCKET_COUNT


def test_plugin_config_string_conversions_success():
    settings = load_plugin_settings(
        {
            "models": {
                "chat_default": "chat-main",
                "ocr": "ocr-model",
                "topic_classifier": "topic-model",
                "tool_intent_classifier": "tool-model",
                "summary": "summary-model",
            },
            "summary": {
                "enabled": "off",
                "trigger_non_bot_count": "5",
                "trigger_silence_minutes": "6",
            },
            "storage": {
                "base_dir": "/tmp/chat-tool-balance",
                "bucket_count": "10",
            },
        }
    )

    assert settings.models.chat_default == "chat-main"
    assert settings.models.ocr == "ocr-model"
    assert settings.models.topic_classifier == "topic-model"
    assert settings.models.tool_intent_classifier == "tool-model"
    assert settings.models.summary == "summary-model"

    assert settings.summary.enabled is False
    assert settings.summary.trigger.trigger_non_bot_count == 5
    assert settings.summary.trigger.trigger_silence_minutes == 6

    assert settings.storage.base_dir == "/tmp/chat-tool-balance"
    assert settings.storage.bucket_count == DEFAULT_BUCKET_COUNT
