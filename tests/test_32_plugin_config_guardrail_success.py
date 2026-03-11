from astrbot_plugin_chat_tool_balance.plugin_config import (
    DEFAULT_NON_BOT_TRIGGER,
    DEFAULT_SILENCE_TRIGGER_MINUTES,
    load_plugin_settings,
)
from astrbot_plugin_chat_tool_balance.storage.path_manager import DEFAULT_BASE_DIR, DEFAULT_BUCKET_COUNT


def test_plugin_config_invalid_values_fallback_success():
    settings = load_plugin_settings(
        {
            "models": {
                "chat_default": " chat-base ",
                "ocr": None,
                "topic_classifier": "",
                "tool_intent_classifier": 123,
                "summary": " ",
                "chat_model": " gpt-4.1 ",
                "ocr_model": None,
                "topic_classifier_model": "",
                "tool_intent_classifier_model": 123,
                "summary_model": " ",
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
    assert settings.models.chat_model == "gpt-4.1"
    assert settings.models.ocr_model == "gpt-4.1"
    assert settings.models.topic_classifier_model == "gpt-4.1"
    assert settings.models.tool_intent_classifier_model == "gpt-4.1"
    assert settings.models.summary_model == "gpt-4.1"

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
                "chat_model": "gpt-4.1-mini",
                "ocr_model": "gpt-4o-mini",
                "topic_classifier_model": "gpt-4.1-mini",
                "tool_intent_classifier_model": "gpt-4.1-mini",
                "summary_model": "gpt-4.1",
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
    assert settings.models.chat_model == "gpt-4.1-mini"
    assert settings.models.ocr_model == "gpt-4o-mini"
    assert settings.models.topic_classifier_model == "gpt-4.1-mini"
    assert settings.models.tool_intent_classifier_model == "gpt-4.1-mini"
    assert settings.models.summary_model == "gpt-4.1"

    assert settings.summary.enabled is False
    assert settings.summary.trigger.trigger_non_bot_count == 5
    assert settings.summary.trigger.trigger_silence_minutes == 6

    assert settings.storage.base_dir == "/tmp/chat-tool-balance"
    assert settings.storage.bucket_count == DEFAULT_BUCKET_COUNT
