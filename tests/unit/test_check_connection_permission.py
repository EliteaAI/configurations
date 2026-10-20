"""check_connection / check_connections require the unsecret permission.

Callers without it are denied before any secret resolution or outbound check;
callers with it keep the existing behaviour.
"""

import functools
import importlib.util
import pathlib
import sys
import types

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
PACKAGE = "configurations_check_conn"

UNSECRET = "configuration.secrets.secret.unsecret"
SECRET_REF = "{{secret.stored_key}}"
RESOLVED = "real-secret-value"
TARGET_URL = "http://example.invalid/"


class Harness:
    """Records what the endpoint did, so a test can prove nothing ran behind a 403."""

    def __init__(self):
        self.user_permissions = set()
        self.descriptors = {}
        self.unsecret_calls = []
        self.check_calls = []
        self.json = None

    def check_api(self, descriptor):
        required = set(descriptor["permissions"])

        def decorator(func):
            self.descriptors[func.__module__] = descriptor

            @functools.wraps(func)
            def wrapper(*args, **kwargs):
                if not required & self.user_permissions:
                    return {"ok": False, "error": "access_denied"}, 403
                return func(*args, **kwargs)
            return wrapper
        return decorator


def _module(name, **attrs):
    module = types.ModuleType(name)
    module.__dict__.update(attrs)
    sys.modules[name] = module
    return module


def _load(harness, filename):
    package = _module(PACKAGE)
    package.__path__ = [str(ROOT)]
    for sub in ("api", "api.v2", "models", "models.pd"):
        _module(f"{PACKAGE}.{sub}").__path__ = []

    class VaultClient:
        def __init__(self, project_id):
            self.project_id = project_id

        def unsecret(self, data):
            harness.unsecret_calls.append((self.project_id, data))
            return {k: (RESOLVED if v == SECRET_REF else v) for k, v in data.items()}

    def register_openapi(**_):
        return lambda func: func

    _module(
        f"{PACKAGE}.local_tools",
        APIBase=type("APIBase", (), {}),
        register_openapi=register_openapi,
        VaultClient=VaultClient,
        config=types.SimpleNamespace(ADMINISTRATION_MODE="administration", DEFAULT_MODE="default"),
    )

    class FakeModel:
        @staticmethod
        def check_connection(data):
            # Stands in for the real outbound HTTP call to data["api_base"]
            harness.check_calls.append(data)
            return None

    _module(f"{PACKAGE}.models.pd.registry", CONFIG_TYPE_REGISTRY={"azure_open_ai": FakeModel})
    _module(f"{PACKAGE}.utils", expand_configuration=lambda **_: None)
    # request.json is read at call time, after the test has set harness.json
    request = type("Request", (), {"json": property(lambda _: harness.json), "args": {}})()
    _module("flask", request=request)
    core = types.SimpleNamespace(module=types.SimpleNamespace(governance_profile=types.SimpleNamespace(enabled=False)))
    _module(
        "tools",
        auth=types.SimpleNamespace(
            decorators=types.SimpleNamespace(check_api=harness.check_api),
            current_user=lambda: {"id": 42},
        ),
        this=types.SimpleNamespace(for_module=lambda name: core),  # governance off: legacy behaviour, unchanged
    )

    module_name = f"{PACKAGE}.api.v2.{filename}"
    spec = importlib.util.spec_from_file_location(module_name, ROOT / "api" / "v2" / f"{filename}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
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
def harness(isolated_modules):
    return Harness()


def _payload():
    return {"api_key": SECRET_REF, "api_base": TARGET_URL, "api_version": "2025-04-01-preview"}


def _call_single(harness):
    module = _load(harness, "check_connection")
    harness.json = _payload()
    return module.API().post(project_id=1, config_type="azure_open_ai")


def _call_batch(harness):
    module = _load(harness, "check_connections")
    harness.json = [{"id": "a", "type": "azure_open_ai", "data": _payload()}]
    return module.API().post(project_id=1)


CALLERS = pytest.mark.parametrize("call", [_call_single, _call_batch], ids=["single", "batch"])
MODULES = pytest.mark.parametrize("filename", ["check_connection", "check_connections"])


@MODULES
def test_endpoint_declares_unsecret_permission(harness, filename):
    _load(harness, filename)
    descriptor = harness.descriptors[f"{PACKAGE}.api.v2.{filename}"]
    assert descriptor["permissions"] == [UNSECRET]


@MODULES
def test_recommended_roles_match_secret_api(harness, filename):
    # Same defaults as secrets/api/v2/secret.py, so the two declarations of one permission agree
    _load(harness, filename)
    roles = harness.descriptors[f"{PACKAGE}.api.v2.{filename}"]["recommended_roles"]
    expected = {"admin": True, "editor": True, "viewer": False}
    assert roles == {"administration": expected, "default": expected}


@CALLERS
def test_caller_without_unsecret_is_denied_before_any_resolution(harness, call):
    # Viewer-like caller: has ordinary read permissions, but not unsecret
    harness.user_permissions = {"models.applications.tool.details"}

    response, status = call(harness)

    assert status == 403
    assert response["error"] == "access_denied"
    assert harness.unsecret_calls == [], "vault must not be read for a denied caller"
    assert harness.check_calls == [], "no outbound request may be made for a denied caller"


@CALLERS
def test_caller_with_unsecret_still_resolves_secret_refs(harness, call):
    # Editor in their own project: the stored-credential test flow must keep working
    harness.user_permissions = {UNSECRET}

    response, status = call(harness)

    assert status == 200
    assert harness.unsecret_calls and harness.unsecret_calls[0][0] == 1
    assert harness.check_calls == [
        {"api_key": RESOLVED, "api_base": TARGET_URL, "api_version": "2025-04-01-preview"}
    ]
