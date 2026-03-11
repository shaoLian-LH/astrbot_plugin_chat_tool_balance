from __future__ import annotations

import json
from pathlib import Path

from plugin_config import load_plugin_settings


def test_gateway_feature_toggle_default_and_legacy_compat_success():
    empty_settings = load_plugin_settings({})
    assert empty_settings.features.use_responses_api is True

    legacy_settings = load_plugin_settings(
        {
            "models": {"chat_default": "legacy-chat"},
            "summary": {"enabled": "off"},
            "storage": {"base_dir": "/tmp/legacy-chat-tool-balance"},
        }
    )
    assert legacy_settings.features.use_responses_api is True
    assert legacy_settings.models.chat_default == "legacy-chat"
    assert legacy_settings.summary.enabled is False


def test_gateway_feature_toggle_string_bool_and_invalid_fallback_success():
    disabled_settings = load_plugin_settings({"features": {"use_responses_api": "off"}})
    assert disabled_settings.features.use_responses_api is False

    enabled_settings = load_plugin_settings({"features": {"use_responses_api": "ON"}})
    assert enabled_settings.features.use_responses_api is True

    invalid_settings = load_plugin_settings({"features": {"use_responses_api": "maybe"}})
    assert invalid_settings.features.use_responses_api is True


def test_gateway_conf_schema_provider_selector_and_feature_default_success():
    schema = json.loads(Path("_conf_schema.json").read_text(encoding="utf-8"))
    properties = schema["properties"]

    feature_schema = properties["features"]["properties"]["use_responses_api"]
    assert feature_schema["type"] == "boolean"
    assert feature_schema["default"] is True

    model_properties = properties["models"]["properties"]
    for field_name in (
        "chat_default",
        "ocr",
        "topic_classifier",
        "tool_intent_classifier",
        "summary",
    ):
        assert model_properties[field_name]["_special"] == "select_provider"

    for model_name_field in (
        "chat_model",
        "ocr_model",
        "topic_classifier_model",
        "tool_intent_classifier_model",
        "summary_model",
    ):
        assert model_properties[model_name_field]["type"] == "string"
        assert model_properties[model_name_field]["default"] == ""
