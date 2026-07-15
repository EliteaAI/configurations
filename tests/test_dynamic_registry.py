"""Dynamic configuration registry reconciliation tests."""

import importlib.util
from pathlib import Path
import sys
import types


PLUGIN_ROOT = Path(__file__).parents[1]


def _load_registry(monkeypatch):
    package = types.ModuleType("configurations")
    package.__path__ = [str(PLUGIN_ROOT)]
    models = types.ModuleType("configurations.models")
    models.__path__ = [str(PLUGIN_ROOT / "models")]
    pd_package = types.ModuleType("configurations.models.pd")
    pd_package.__path__ = [str(PLUGIN_ROOT / "models" / "pd")]
    local_tools = types.ModuleType("configurations.local_tools")
    local_tools.log = types.SimpleNamespace(info=lambda *_: None, error=lambda *_: None)

    monkeypatch.setitem(sys.modules, "configurations", package)
    monkeypatch.setitem(sys.modules, "configurations.models", models)
    monkeypatch.setitem(sys.modules, "configurations.models.pd", pd_package)
    monkeypatch.setitem(sys.modules, "configurations.local_tools", local_tools)

    spec = importlib.util.spec_from_file_location(
        "configurations.models.pd.registry",
        PLUGIN_ROOT / "models" / "pd" / "registry.py",
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_replace_updates_dynamic_schema(monkeypatch):
    registry = _load_registry(monkeypatch)
    registry.register_config_type(
        "mcp_example",
        "toolkits",
        validation_func="validate",
        config_schema={"title": "Old"},
    )

    registry.register_config_type(
        "mcp_example",
        "toolkits",
        validation_func="validate",
        config_schema={"title": "New"},
        replace=True,
    )

    assert registry.CONFIG_TYPE_REGISTRY["mcp_example"].config_schema == {"title": "New"}


def test_failed_replace_preserves_previous_registration(monkeypatch):
    registry = _load_registry(monkeypatch)
    registry.register_config_type(
        "mcp_example",
        "toolkits",
        validation_func="validate",
        config_schema={"title": "Old"},
    )

    old = registry.CONFIG_TYPE_REGISTRY["mcp_example"]
    registry.register_config_type(
        "mcp_example",
        "toolkits",
        config_schema={"title": "Invalid without validator"},
        replace=True,
    )

    assert registry.CONFIG_TYPE_REGISTRY["mcp_example"] is old


def test_unregister_removes_only_requested_type(monkeypatch):
    registry = _load_registry(monkeypatch)
    registry.register_config_type(
        "mcp_one", "toolkits", validation_func="validate", config_schema={"type": "object"},
    )
    registry.register_config_type(
        "mcp_two", "toolkits", validation_func="validate", config_schema={"type": "object"},
    )

    assert registry.unregister_config_type("mcp_one") is True
    assert registry.unregister_config_type("mcp_one") is False
    assert "mcp_two" in registry.CONFIG_TYPE_REGISTRY
