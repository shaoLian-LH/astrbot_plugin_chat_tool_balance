from types import SimpleNamespace

from services.runtime_wiring import build_livingmemory_client_getter, extract_raw_plugin_config


def test_runtime_wiring_extract_raw_plugin_config_prioritize_plugin_mapping_success():
    context = SimpleNamespace(config={"source": "context"})
    plugin = SimpleNamespace(config={"source": "plugin", "enabled": True})

    raw_config = extract_raw_plugin_config(context, plugin)

    assert raw_config == {"source": "plugin", "enabled": True}


def test_runtime_wiring_extract_raw_plugin_config_from_named_getter_success():
    class _NamedConfigContext:
        def get_plugin_config(self, plugin_name: str):
            if plugin_name == "astrbot_plugin_chat_tool_balance":
                return {"models": {"chat_default": "chat-main"}}
            return None

    raw_config = extract_raw_plugin_config(_NamedConfigContext(), SimpleNamespace())

    assert raw_config == {"models": {"chat_default": "chat-main"}}


def test_runtime_wiring_extract_raw_plugin_config_after_getter_error_success():
    class _FallbackContext:
        def get_config(self):
            raise RuntimeError("config backend unavailable")

        def get_plugin_conf(self):
            return {"summary": {"enabled": False}}

    raw_config = extract_raw_plugin_config(_FallbackContext(), SimpleNamespace())

    assert raw_config == {"summary": {"enabled": False}}


def test_runtime_wiring_livingmemory_getter_prefers_context_plugin_success():
    client = object()

    class _Context:
        plugin_manager = SimpleNamespace(
            get_plugin=lambda _name: (_ for _ in ()).throw(AssertionError("should_not_use_plugin_manager"))
        )

        def get_plugin(self, name: str):
            if name == "livingmemory":
                return client
            return None

    getter = build_livingmemory_client_getter(_Context())

    assert getter() is client


def test_runtime_wiring_livingmemory_getter_fallback_to_plugin_manager_success():
    client = object()

    class _PluginManager:
        def get_service(self, name: str):
            if name == "astrbot_plugin_livingmemory_v2":
                return client
            return None

    context = SimpleNamespace(plugin_manager=_PluginManager())
    getter = build_livingmemory_client_getter(context)

    assert getter() is client


def test_runtime_wiring_livingmemory_getter_returns_none_when_unavailable_success():
    class _BrokenPluginManager:
        def get_plugin(self, _name: str):
            raise RuntimeError("lookup failed")

    class _BrokenContext:
        plugin_manager = _BrokenPluginManager()

        def get_plugin(self, _name: str):
            raise RuntimeError("context lookup failed")

    getter = build_livingmemory_client_getter(_BrokenContext())

    assert getter() is None
