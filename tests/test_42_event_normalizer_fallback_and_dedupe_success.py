from datetime import datetime

from astrbot_plugin_chat_tool_balance.handlers.event_normalizer import is_status_command_message, normalize_event


class _ImageMessagePart:
    def __init__(self, url: str):
        self.type = "img"
        self.url = url


class _RichFallbackEvent:
    message_str = " "
    text = None
    message = None
    sender_id = None
    user_id = None
    from_user_id = None
    group_id = " g-room "
    session_id = None
    conversation_id = None
    unified_msg_origin = None
    message_id = None
    msg_id = None
    id = None
    created_at = datetime(2026, 3, 10, 8, 30, 0)
    image_urls = [" https://example.com/a.png ", {"url": "https://example.com/b.png"}]
    images = {"src": "https://example.com/b.png"}
    is_bot = None
    is_self = None
    from_bot = None
    conversation_type = " "
    platform = " "

    @staticmethod
    def get_message_str():
        raise RuntimeError("message_str unavailable")

    @staticmethod
    def get_text():
        return "  hello normalized  "

    @staticmethod
    def get_sender_id():
        return " user-getter "

    @staticmethod
    def get_message_id():
        return None

    @staticmethod
    def get_msg_id():
        raise RuntimeError("message id unavailable")

    @staticmethod
    def get_extra(key: str, default=None):
        if key == "image_urls":
            return ["https://example.com/c.png", "https://example.com/a.png"]
        if key == "from_bot":
            return True
        return default

    @staticmethod
    def get_messages():
        return [
            {"type": "image", "url": "https://example.com/d.png"},
            {"type": "text", "url": "https://example.com/ignored.png"},
            _ImageMessagePart("https://example.com/e.png"),
            _ImageMessagePart(" https://example.com/e.png "),
        ]

    @staticmethod
    def get_conversation_type():
        return "GROUP"

    @staticmethod
    def get_platform():
        return "qq"


class _MinimalFallbackEvent:
    @staticmethod
    def get_message_str():
        raise RuntimeError("no message")

    @staticmethod
    def get_text():
        raise RuntimeError("no text")

    @staticmethod
    def get_plain_text():
        return " "

    @staticmethod
    def get_sender_id():
        return None

    @staticmethod
    def get_user_id():
        return None

    @staticmethod
    def get_group_id():
        return None

    @staticmethod
    def get_session_id():
        return None

    @staticmethod
    def get_conversation_id():
        return None

    @staticmethod
    def get_message_id():
        return None

    @staticmethod
    def get_msg_id():
        return None

    @staticmethod
    def get_extra(_key: str, default=None):
        return default

    @staticmethod
    def get_messages():
        return ()


def test_event_normalizer_fallback_paths_and_image_dedupe_success():
    normalized = normalize_event(_RichFallbackEvent())

    assert normalized.text == "hello normalized"
    assert normalized.user_id == "user-getter"
    assert normalized.scope_id == "group:g-room"
    assert normalized.session_id == "group:g-room"
    assert normalized.message_id.startswith("msg_")
    assert len(normalized.message_id) == 20
    assert normalized.created_at == "2026-03-10T08:30:00+00:00"
    assert normalized.image_urls == (
        "https://example.com/a.png",
        "https://example.com/b.png",
        "https://example.com/c.png",
        "https://example.com/d.png",
        "https://example.com/e.png",
    )
    assert normalized.is_bot is True
    assert normalized.role == "assistant"
    assert normalized.metadata["conversation_type"] == "group"
    assert normalized.metadata["group_id"] == "g-room"
    assert normalized.metadata["platform"] == "qq"
    assert normalized.metadata["unified_msg_origin"] == ""


def test_event_normalizer_private_defaults_and_status_command_detection_success():
    normalized = normalize_event(_MinimalFallbackEvent())

    assert normalized.text == ""
    assert normalized.user_id == "unknown_user"
    assert normalized.scope_id == "private:unknown_user"
    assert normalized.session_id == "private:unknown_user"
    assert normalized.message_id.startswith("msg_")
    assert normalized.image_urls == ()
    assert normalized.is_bot is False
    assert normalized.role == "user"
    assert normalized.metadata["conversation_type"] == "private"
    assert normalized.metadata["group_id"] == ""
    assert normalized.metadata["platform"] == ""
    assert normalized.metadata["unified_msg_origin"] == ""
    assert datetime.fromisoformat(normalized.created_at).tzinfo is not None

    assert is_status_command_message("ctb_status")
    assert is_status_command_message(" /CTB_STATUS ")
    assert not is_status_command_message("ctb_status now")
