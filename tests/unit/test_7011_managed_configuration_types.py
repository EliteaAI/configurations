"""EL-7011 owner CR 2026-10-21: a plugin may own a configuration type.

The REST API refuses create, update and delete of an owned type with 403; reads and the RPC path stay open.
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
FILLED_URL = "/api/v2/elitea_core/schedules/default/7/settings"
REFUSED = {
    "code": "managed_configuration",
    "error": f"{OWNED} is managed by elitea_core; change it through {FILLED_URL}",
    "manage_url": FILLED_URL,
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
        self.stored_title = None
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

            def first(self):
                return (harness.stored_type, {}, harness.stored_title) if harness.stored_type else None

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
        _module(f"{API_PACKAGE}.models.configuration", Configuration=types.SimpleNamespace(type="type", data="data", elitea_title="elitea_title"))
        _module(f"{API_PACKAGE}.models.pd.configuration", ConfigurationCreateBase=model,
                ConfigurationDetails=model, ConfigurationUpdate=model)
        _module(f"{API_PACKAGE}.models.pd.registry",
                managed_configuration_error=self.registry.managed_configuration_error,
                ADMIN_MANAGED_TYPES=self.registry.ADMIN_MANAGED_TYPES)
        _module(f"{API_PACKAGE}.tracing_access", can_manage_tracing=lambda *_: True,
                can_manage_stored_login=lambda *_: True, stored_login_changed=lambda *_: False,
                is_tracing_type=lambda _type: False)
        _module(f"{API_PACKAGE}.utils", get_configurations=record("list", {}),
                create_configuration=record("create", {"id": 1}),
                update_configuration=record("update", {"id": 1}),
                delete_configuration=record("delete", True),
                get_options_for_nested_fields=record("options", {}))
        _module(f"{API_PACKAGE}.exceptions", ConfigurationError=type("ConfigurationError", (Exception,), {}),
                handle_validation_error=lambda error: error)
        # Credential reads decorate admin-app credentials (#7149); nothing to do here
        _module(f"{API_PACKAGE}.oauth_apps", decorate_credentials=lambda *_: None)
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
    assert registry.managed_configuration_error(OWNED, project_id=7) == REFUSED


def test_el7011_cr_an_owner_without_a_url_still_names_itself(registry):
    registry.register_config_type("owned_no_url", "project_settings", validation_func="validate",
                                  config_schema={"title": "X"}, managed_by="elitea_core")
    assert registry.managed_configuration_error("owned_no_url") == {
        "code": "managed_configuration",
        "error": "owned_no_url is managed by elitea_core; change it through the elitea_core API",
        "manage_url": None,
    }


def test_the_manage_url_is_never_a_template(registry):
    assert registry.managed_configuration_error(OWNED)["manage_url"] is None
    assert registry.manage_url_for(registry.CONFIG_TYPE_REGISTRY[OWNED], 7) == FILLED_URL


def test_an_owned_title_is_reserved_for_any_type(registry):
    assert registry.managed_configuration_error("project_context", OWNED, 7) == REFUSED
    assert registry.managed_configuration_error(["unhashable"], "free_title") is None


def test_el7011_cr_rest_create_of_an_owned_type_is_refused(harness):
    harness.json = {"type": OWNED, "elitea_title": OWNED, "data": {"allow_run_without_asking": True}}
    assert harness.load("configurations").post(project_id=7) == (REFUSED, 403)
    assert harness.store_calls == []


def test_rest_create_with_a_reserved_title_is_refused_for_any_type(harness):
    harness.json = {"type": "project_context", "elitea_title": OWNED, "data": {}}
    assert harness.load("configurations").post(project_id=7) == (REFUSED, 403)
    assert harness.store_calls == []


def test_rest_create_with_a_non_string_type_is_a_bad_request(harness):
    harness.json = {"type": ["x"], "data": {}}
    body, status = harness.load("configurations").post(project_id=7)
    assert (status, body["field"]) == (400, "type")
    assert harness.store_calls == []


def test_rest_rename_to_a_reserved_title_is_refused(harness):
    harness.stored_type = "project_context"
    harness.json = {"elitea_title": OWNED}
    assert harness.load("configuration").put(project_id=7, config_id=5) == (REFUSED, 403)
    assert harness.store_calls == []


def test_el7011_cr_rest_create_of_a_plugin_type_goes_on(harness):
    harness.json = {"type": "project_context", "data": {}}
    assert harness.load("configurations").post(project_id=7) == ({"id": 1}, 200)
    assert harness.store_calls == ["create"]


def test_el7011_cr_rest_update_of_an_owned_record_is_refused(harness):
    harness.stored_type = OWNED
    harness.json = {"data": {"allow_run_without_asking": True}}
    assert harness.load("configuration").put(project_id=7, config_id=5) == (REFUSED, 403)
    assert harness.store_calls == []


def test_el7011_cr_rest_delete_of_an_owned_record_is_refused(harness):
    harness.stored_type = OWNED
    result = harness.load("configuration").delete(project_id=7, config_id=5)
    assert result == (REFUSED, 403)
    assert harness.store_calls == []


def test_el7011_cr_rest_update_and_delete_of_a_plugin_type_go_on(harness):
    harness.stored_type = "project_context"
    harness.json = {"data": {}}
    api = harness.load("configuration")
    put_result = api.put(project_id=7, config_id=5)
    delete_result = api.delete(project_id=7, config_id=5)
    assert put_result[1] == 200
    assert delete_result[1] == 204
    assert harness.store_calls == ["update", "delete"]


def test_el7011_cr_the_register_rpc_passes_the_owner_on(registry, isolated_modules):
    """The register RPC carries the owner fields to the registry."""
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


warnings = []


@pytest.fixture
def utils(registry):
    warnings.clear()
    package = "configurations_7011_utils"
    _module(package).__path__ = [str(ROOT)]
    for sub in ("models", "models.pd"):
        _module(f"{package}.{sub}").__path__ = []
    _module(f"{package}.common_utils", get_personal_project_id=None, get_public_project_id=None)
    _module(f"{package}.local_tools", db=None, store_secrets=None, purge_secrets=None, event_manager=None,
            log=types.SimpleNamespace(warning=lambda *args: warnings.append(args)), VaultClient=None)
    _module(f"{package}.models.configuration", Configuration=object)
    _module(f"{package}.models.pd.configuration", ConfigurationCreate=None, ConfigurationDetails=None,
            ConfigurationCreateRpc=None, ConfigurationList=None)
    _module(f"{package}.models.pd.registry", CONFIG_TYPE_REGISTRY=registry.CONFIG_TYPE_REGISTRY,
            owned_entry=registry.owned_entry, ADMIN_MANAGED_TYPES=registry.ADMIN_MANAGED_TYPES)
    _module(f"{package}.folder_access", folder_exclusion_clause=None)
    _module(f"{package}.llm_model_profiles", check_llm_model_profile_bounds=None)
    # Admin-app credentials (#7149) are not involved in these writes
    _module(f"{package}.oauth_apps", OAUTH_APP_TOOLKIT_TYPES=(), apply_oauth_app=lambda *_: None,
            record_login_client=lambda *_: None, reject_shared=lambda *_: None,
            resolve_on_use=lambda *_: None, strip_app_client_fields=lambda *_, **__: None,
            decorate_credentials=lambda *_: None)
    _exec(f"{package}.exceptions", ROOT / "exceptions.py")
    return _exec(f"{package}.utils", ROOT / "utils.py")


def test_a_write_of_an_owned_entry_refuses_another_owner(utils):
    for type_name, title in ((OWNED, None), ("project_context", OWNED), ("project_context", " Schedule_Settings ")):
        with pytest.raises(utils.ConfigurationError):
            utils.assert_write_allowed(type_name, title, "other_plugin")
        utils.assert_write_allowed(type_name, title, "elitea_core")
        utils.assert_write_allowed(type_name, title, None)
    utils.assert_write_allowed("project_context", "free_title", "other_plugin")


def test_a_write_of_an_owned_entry_without_an_owner_logs_a_warning(utils):
    utils.assert_write_allowed(OWNED, None, None)
    assert len(warnings) == 1 and OWNED in warnings[0]
    utils.assert_write_allowed("project_context", "free_title", None)
    utils.assert_write_allowed(OWNED, None, "elitea_core")
    assert len(warnings) == 1


def test_an_unregister_refuses_another_owner_and_keeps_the_owner_on_register(registry):
    with pytest.raises(ValueError):
        registry.unregister_config_type(OWNED, "other_plugin")
    assert OWNED in registry.CONFIG_TYPE_REGISTRY
    assert registry.unregister_config_type(OWNED, "elitea_core") is True
    assert registry.unregister_config_type("llm_model") is True
    assert registry.unregister_config_type("llm_model") is False


def test_a_replace_cannot_drop_or_change_the_owner(registry):
    with pytest.raises(ValueError):
        registry.register_config_type(OWNED, "settings", replace=True)
    with pytest.raises(ValueError):
        registry.register_config_type(OWNED, "settings", replace=True, managed_by="other_plugin")
    assert registry.CONFIG_TYPE_REGISTRY[OWNED].managed_by == "elitea_core"
    registry.register_config_type(OWNED, "settings", replace=True, managed_by="elitea_core")
    assert registry.CONFIG_TYPE_REGISTRY[OWNED].managed_by == "elitea_core"


def _rpc(registry, calls):
    package = "configurations_7011_rpc_owner"
    _module(package).__path__ = [str(ROOT)]
    _module(f"{package}.rpc").__path__ = []
    _module(f"{package}.models").__path__ = []
    _module(f"{package}.models.pd").__path__ = []
    _module(f"{package}.local_tools", log=None)
    update_model = type("Model", (), {"model_validate": staticmethod(
        lambda data: types.SimpleNamespace(model_dump=lambda **_: dict(data)))})
    _module(f"{package}.models.pd.configuration", ConfigurationUpdateRpc=update_model)
    _module(f"{package}.models.pd.registry", CONFIG_TYPE_REGISTRY={},
            unregister_config_type=lambda *args: calls.append(("unregister", *args)) or True,
            register_config_type=None)
    _module(f"{package}.utils",
            update_configuration=lambda *args, **kwargs: calls.append(("update", kwargs)) or {},
            create_if_not_exists=lambda *args, **kwargs: calls.append(("create", kwargs)) or ({}, True),
            expand_configuration=None,
            delete_configuration=lambda *args, **kwargs: calls.append(("delete", kwargs)) or {})
    return _exec(f"{package}.rpc.main", ROOT / "rpc" / "main.py").RPC()


def test_the_rpc_passes_the_caller_as_owner(registry):
    calls = []
    rpc = _rpc(registry, calls)
    rpc.update_configuration_rpc(7, 5, {"data": {}}, owner="elitea_core")
    rpc.configurations_delete(7, 5, owner="elitea_core")
    rpc.create_if_not_exists({"type": OWNED}, owner="elitea_core")
    assert [kwargs["owner"] for _, kwargs in calls] == ["elitea_core"] * 3


def test_the_unregister_rpc_passes_the_caller_as_owner(registry):
    calls = []
    rpc = _rpc(registry, calls)
    rpc.configurations_unregister(OWNED, owner="elitea_core")
    assert calls == [("unregister", OWNED, "elitea_core")]


def test_the_rpc_without_a_caller_passes_no_owner(registry):
    calls = []
    rpc = _rpc(registry, calls)
    rpc.update_configuration_rpc(7, 5, {"data": {}})
    rpc.configurations_delete(7, 5)
    rpc.create_if_not_exists({"type": OWNED})
    assert [kwargs["owner"] for _, kwargs in calls] == [None] * 3


def test_a_rename_to_a_reserved_title_is_refused_but_an_old_reserved_title_stays_editable(harness):
    harness.stored_type = "project_context"
    harness.stored_title = OWNED
    harness.json = {"elitea_title": OWNED, "data": {}}
    assert harness.load("configuration").put(project_id=7, config_id=5)[1] == 200
    harness.stored_title = "free_title"
    assert harness.load("configuration").put(project_id=7, config_id=5) == (REFUSED, 403)


def test_available_lists_the_owner_and_the_filled_manage_url(registry):
    package = "configurations_7011_available"
    _module(package).__path__ = [str(ROOT)]
    for sub in ("api", "api.v2", "models", "models.pd"):
        _module(f"{package}.{sub}").__path__ = []
    args = types.SimpleNamespace(getlist=lambda _name: [], get=lambda *_a, **_k: None)
    _module("flask", request=types.SimpleNamespace(args=args))
    _module(f"{package}.local_tools", APIBase=type("APIBase", (), {}), register_openapi=lambda **_: (lambda f: f))
    _module(f"{package}.models.pd.configuration", ConfigurationCreateBase=types.SimpleNamespace(
        model_json_schema=lambda: {"properties": {}}))
    _module(f"{package}.models.pd.registry", CONFIG_TYPE_REGISTRY={OWNED: registry.CONFIG_TYPE_REGISTRY[OWNED]},
            manage_url_for=registry.manage_url_for)
    _module(f"{package}.tracing_access", current_actor_id=lambda: 1, hidden_config_types=lambda *_: set())
    api = _exec(f"{package}.api.v2.available", ROOT / "api" / "v2" / "available.py").API()
    items, status = api.get(project_id=7)
    assert status == 200
    assert (items[0]["managed_by"], items[0]["manage_url"]) == ("elitea_core", FILLED_URL)
