"""EL-7011 owner CR 2026-10-21: a plugin may own a configuration type.

The REST API refuses create, update and delete of an owned type with 409; reads and the RPC path stay open.
"""

import importlib.util
import pathlib
import sys
import types

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
REGISTRY_PACKAGE = "configurations_7011_registry"
API_PACKAGE = "configurations_7011_api"
OWNED = "schedule_settings"
MANAGE_URL = "/api/v2/elitea_core/schedules/{mode}/{project_id}/settings"
REFUSED = {
    "code": "managed_configuration",
    "error": f"{OWNED} is managed by elitea_core; change it through {MANAGE_URL}",
    "manage_url": MANAGE_URL,
}


def _module(name, **attrs):
    module = types.ModuleType(name)
    module.__dict__.update(attrs)
    sys.modules[name] = module
    return module


def _exec(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def isolated_modules():
    saved = sys.modules.copy()
    try:
        yield
    finally:
        for key in set(sys.modules) - set(saved):
            sys.modules.pop(key, None)
        sys.modules.update(saved)


@pytest.fixture
def registry(isolated_modules):
    """The real registry module, with the plugin's own types and one owned type."""
    _module(REGISTRY_PACKAGE).__path__ = [str(ROOT)]
    _module(f"{REGISTRY_PACKAGE}.models").__path__ = [str(ROOT / "models")]
    _module(f"{REGISTRY_PACKAGE}.models.pd").__path__ = [str(ROOT / "models" / "pd")]
    _module(f"{REGISTRY_PACKAGE}.local_tools", rpc_manager=None,
            log=types.SimpleNamespace(info=lambda *_: None, error=lambda *_: None))
    module = _exec(f"{REGISTRY_PACKAGE}.models.pd.registry", ROOT / "models" / "pd" / "registry.py")
    module.register_config_type(OWNED, "project_settings", validation_func="validate",
                                config_schema={"title": "Schedules"}, replace=True,
                                managed_by="elitea_core", manage_url=MANAGE_URL)
    return module


class Harness:
    """Stands in for the platform around the REST handlers and records each store call."""

    def __init__(self, registry):
        self.registry = registry
        self.json = None
        self.stored_type = None
        self.store_calls = []

    def load(self, filename):
        _module(API_PACKAGE).__path__ = [str(ROOT)]
        for sub in ("api", "api.v2", "models", "models.pd"):
            _module(f"{API_PACKAGE}.{sub}").__path__ = []
        passthrough = lambda *_, **__: (lambda func: func)
        harness = self

        class Session:
            def query(self, *_):
                return self

            def filter_by(self, **_):
                return self

            def scalar(self):
                return harness.stored_type

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

        def record(name, result):
            def call(*args, **kwargs):
                self.store_calls.append(name)
                return result
            return call

        model = type("Model", (), {"model_validate": staticmethod(
            lambda data: types.SimpleNamespace(model_dump=lambda **_: dict(data or {})))})
        auth = types.SimpleNamespace(decorators=types.SimpleNamespace(check_api=passthrough),
                                     current_user=lambda: {"id": 42})
        _module("flask", request=type("Request", (), {"json": property(lambda _: harness.json)})())
        _module("tools", api_tools=types.SimpleNamespace(endpoint_metrics=lambda func: func),
                rpc_tools=types.SimpleNamespace())
        _module(f"{API_PACKAGE}.local_tools", APIBase=type("APIBase", (), {}), auth=auth,
                current_user=auth.current_user, register_openapi=passthrough,
                log=types.SimpleNamespace(warning=lambda *_: None, exception=lambda *_: None),
                db=types.SimpleNamespace(get_session=lambda _project_id: Session()),
                config=types.SimpleNamespace(ADMINISTRATION_MODE="administration", DEFAULT_MODE="default"),
                rpc_manager=None)
        _module(f"{API_PACKAGE}.models.configuration", Configuration=types.SimpleNamespace(type="type"))
        _module(f"{API_PACKAGE}.models.pd.configuration", ConfigurationCreateBase=model,
                ConfigurationDetails=model, ConfigurationUpdate=model)
        _module(f"{API_PACKAGE}.models.pd.registry",
                managed_configuration_error=self.registry.managed_configuration_error)
        _module(f"{API_PACKAGE}.tracing_access", can_manage_tracing=lambda *_: True,
                is_tracing_type=lambda _type: False)
        _module(f"{API_PACKAGE}.utils", get_configurations=record("list", {}),
                create_configuration=record("create", {"id": 1}),
                update_configuration=record("update", {"id": 1}),
                delete_configuration=record("delete", True),
                get_options_for_nested_fields=record("options", {}))
        _module(f"{API_PACKAGE}.exceptions", ConfigurationError=type("ConfigurationError", (Exception,), {}))
        _module(f"{API_PACKAGE}.common_utils", get_public_project_id=lambda: 1)
        _module(f"{API_PACKAGE}.folder_access", require_folder_access=passthrough)
        return _exec(f"{API_PACKAGE}.api.v2.{filename}", ROOT / "api" / "v2" / f"{filename}.py").API()


@pytest.fixture
def harness(registry):
    return Harness(registry)


def test_el7011_cr_a_registration_keeps_its_owner(registry):
    entry = registry.CONFIG_TYPE_REGISTRY[OWNED]
    assert (entry.managed_by, entry.manage_url) == ("elitea_core", MANAGE_URL)


def test_el7011_cr_plugin_types_have_no_owner(registry):
    assert registry.CONFIG_TYPE_REGISTRY["project_context"].managed_by is None
    assert registry.managed_configuration_error("project_context") is None


@pytest.mark.parametrize("type_name", [None, "", "not_registered"])
def test_el7011_cr_an_unknown_type_is_not_owned(registry, type_name):
    assert registry.managed_configuration_error(type_name) is None


def test_el7011_cr_the_owned_type_answers_the_refusal(registry):
    assert registry.managed_configuration_error(OWNED) == REFUSED


def test_el7011_cr_an_owner_without_a_url_still_names_itself(registry):
    registry.register_config_type("owned_no_url", "project_settings", validation_func="validate",
                                  config_schema={"title": "X"}, managed_by="elitea_core")
    assert registry.managed_configuration_error("owned_no_url") == {
        "code": "managed_configuration",
        "error": "owned_no_url is managed by elitea_core; change it through the elitea_core API",
        "manage_url": None,
    }


def test_el7011_cr_rest_create_of_an_owned_type_is_refused(harness):
    harness.json = {"type": OWNED, "elitea_title": OWNED, "data": {"allow_run_without_asking": True}}
    assert harness.load("configurations").post(project_id=7) == (REFUSED, 409)
    assert harness.store_calls == []


def test_el7011_cr_rest_create_of_a_plugin_type_goes_on(harness):
    harness.json = {"type": "project_context", "data": {}}
    assert harness.load("configurations").post(project_id=7) == ({"id": 1}, 200)
    assert harness.store_calls == ["create"]


def test_el7011_cr_rest_update_of_an_owned_record_is_refused(harness):
    harness.stored_type = OWNED
    harness.json = {"data": {"allow_run_without_asking": True}}
    assert harness.load("configuration").put(project_id=7, config_id=5) == (REFUSED, 409)
    assert harness.store_calls == []


def test_el7011_cr_rest_delete_of_an_owned_record_is_refused(harness):
    harness.stored_type = OWNED
    assert harness.load("configuration").delete(project_id=7, config_id=5) == (REFUSED, 409)
    assert harness.store_calls == []


def test_el7011_cr_rest_update_and_delete_of_a_plugin_type_go_on(harness):
    harness.stored_type = "project_context"
    harness.json = {"data": {}}
    api = harness.load("configuration")
    assert api.put(project_id=7, config_id=5)[1] == 200
    assert api.delete(project_id=7, config_id=5)[1] == 204
    assert harness.store_calls == ["update", "delete"]


def test_el7011_cr_the_register_rpc_passes_the_owner_on(registry, isolated_modules):
    """The RPC the owner plugin calls; its update and delete RPCs carry no owner check."""
    calls = []
    package = "configurations_7011_rpc"
    _module(package).__path__ = [str(ROOT)]
    _module(f"{package}.rpc").__path__ = []
    _module(f"{package}.models").__path__ = []
    _module(f"{package}.models.pd").__path__ = []
    _module(f"{package}.local_tools", log=None)
    _module(f"{package}.models.pd.configuration", ConfigurationUpdateRpc=None)
    _module(f"{package}.models.pd.registry", CONFIG_TYPE_REGISTRY={}, unregister_config_type=None,
            register_config_type=lambda **kwargs: calls.append(kwargs))
    _module(f"{package}.utils", update_configuration=None, create_if_not_exists=None,
            expand_configuration=None, delete_configuration=None)
    rpc = _exec(f"{package}.rpc.main", ROOT / "rpc" / "main.py").RPC()

    rpc.configurations_register(type_name=OWNED, section="project_settings", validation_func="validate",
                                replace=True, managed_by="elitea_core", manage_url=MANAGE_URL)

    assert calls[0]["managed_by"] == "elitea_core"
    assert calls[0]["manage_url"] == MANAGE_URL
