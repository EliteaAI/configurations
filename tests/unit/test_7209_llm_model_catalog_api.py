"""GET llm_model_catalog relays the gateway's catalog to the LLM model form and never fails the form.

Guards: project readers may call it, the RPC gets its timeout budget, and a missing, failing or malformed RPC
answer becomes an empty catalog with a 200 so the form falls back to "not recognised, all settings".
"""
import functools
import importlib.util
import pathlib
import sys
import types

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
PACKAGE = "configurations_llm_catalog"

LIST_PERMISSION = "configurations.configurations.list"
CATALOG = {
    "prefixes": {"open_ai": "openai"},
    "models": {"gpt-5.6-luna": {"supports_reasoning": True, "supported_efforts": ["low"], "default_effort": "low"}},
}
EMPTY_CATALOG = {"prefixes": {}, "models": {}}


class CatalogRpc:

    def __init__(self, result=None, raises=None):
        self.result = result
        self.raises = raises
        self.timeouts = []

    def timeout(self, seconds):
        self.timeouts.append(seconds)
        return self

    def litellm_llm_model_catalog(self):
        if self.raises:
            raise self.raises
        return self.result


def _module(name, **attrs):
    module = types.ModuleType(name)
    module.__dict__.update(attrs)
    sys.modules[name] = module
    return module


@pytest.fixture
def endpoint():
    saved = sys.modules.copy()
    state = types.SimpleNamespace(user_permissions={LIST_PERMISSION}, descriptor=None, rpc=CatalogRpc(CATALOG))

    def check_api(descriptor):
        state.descriptor = descriptor

        def decorator(func):
            @functools.wraps(func)
            def wrapper(*args, **kwargs):
                if not set(descriptor["permissions"]) & state.user_permissions:
                    return {"ok": False, "error": "access_denied"}, 403
                return func(*args, **kwargs)
            return wrapper
        return decorator

    _module(PACKAGE).__path__ = [str(ROOT)]
    for sub in ("api", "api.v2"):
        _module(f"{PACKAGE}.{sub}").__path__ = []
    rpc_proxy = types.SimpleNamespace(timeout=lambda seconds: state.rpc.timeout(seconds))
    _module(
        f"{PACKAGE}.local_tools",
        APIBase=type("APIBase", (), {}),
        register_openapi=lambda **_: (lambda func: func),
        auth=types.SimpleNamespace(decorators=types.SimpleNamespace(check_api=check_api)),
        config=types.SimpleNamespace(ADMINISTRATION_MODE="administration", DEFAULT_MODE="default"),
        log=types.SimpleNamespace(warning=lambda *args: None),
        rpc_manager=rpc_proxy,
    )
    _module("tools", api_tools=types.SimpleNamespace(endpoint_metrics=lambda func: func))
    module_name = f"{PACKAGE}.api.v2.llm_model_catalog"
    spec = importlib.util.spec_from_file_location(module_name, ROOT / "api" / "v2" / "llm_model_catalog.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    state.get = lambda: module.API().get(project_id=7)
    try:
        yield state
    finally:
        for key in set(sys.modules) - set(saved):
            sys.modules.pop(key, None)
        sys.modules.update(saved)


def test_the_gateway_catalog_is_relayed_inside_the_rpc_timeout(endpoint):
    assert endpoint.get() == (CATALOG, 200)
    assert endpoint.rpc.timeouts == [12]


def test_project_readers_are_allowed_and_others_are_denied_before_the_rpc(endpoint):
    assert endpoint.descriptor["permissions"] == [LIST_PERMISSION]
    for mode in ("administration", "default"):
        assert endpoint.descriptor["recommended_roles"][mode] == {"admin": True, "editor": True, "viewer": True}
    endpoint.user_permissions = set()
    assert endpoint.get()[1] == 403
    assert endpoint.rpc.timeouts == []


@pytest.mark.parametrize("rpc", [
    CatalogRpc(raises=RuntimeError("Timeout: no response from litellm_llm_model_catalog")),
    CatalogRpc(raises=AttributeError("litellm_llm_model_catalog")),
    CatalogRpc(result=None),
    CatalogRpc(result=["not", "a", "catalog"]),
    CatalogRpc(result={"prefixes": None, "models": "broken"}),
])
def test_an_unavailable_catalog_is_an_empty_catalog_not_an_error(endpoint, rpc):
    endpoint.rpc = rpc
    assert endpoint.get() == (EMPTY_CATALOG, 200)


def test_only_the_prefixes_and_models_are_relayed(endpoint):
    endpoint.rpc = CatalogRpc({**CATALOG, "debug": {"gateway": "http://127.0.0.1:8081"}})
    assert endpoint.get()[0] == CATALOG
