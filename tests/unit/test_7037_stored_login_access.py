"""Only project admins (or a personal-project owner) may log in or out on a credential.

Everyone in a team project runs toolkits on a team credential's stored Microsoft login, so setting,
clearing or replacing its ``refresh_token`` acts on behalf of the whole team and is admin-only. Other
credential edits stay open to editors.
"""

import importlib.util
import pathlib
import sys
import types

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]

PACKAGE = "configurations_7037"


class FakeRegistryEntry:
    def __init__(self, config_schema):
        self.config_schema = config_schema


class FakeRpc:
    """RPC methods live only on the proxy returned by timeout(), so an untimed call
    fails the way a missing budget should be noticed - loudly, in a test."""

    def __init__(self, admin_project_users=None, raises=False, personal_projects=None):
        self.admin_project_users = admin_project_users or {}
        self.personal_projects = personal_projects or {}
        self.raises = raises
        self.extra_roles = {}
        self.timeouts = []
        self.admin_calls = 0

    def timeout(self, seconds):
        self.timeouts.append(seconds)
        return _TimedRpc(self)


class _TimedRpc:
    def __init__(self, rpc):
        self._rpc = rpc

    def admin_get_user_roles(self, project_id, user_id):
        self._rpc.admin_calls += 1
        if self._rpc.raises:
            raise RuntimeError("rpc unavailable")
        if user_id in self._rpc.admin_project_users.get(project_id, set()):
            return [{"name": "admin"}]
        return [{"name": name} for name in self._rpc.extra_roles.get((project_id, user_id), [])]

    def projects_get_personal_project_id(self, user_id):
        return self._rpc.personal_projects.get(user_id)


def load_tracing_access(registry=None, rpc=None, personal_projects=None, actor=None):
    """Stubs the plugin's relative imports so the module loads outside a Pylon runtime."""
    package = types.ModuleType(PACKAGE)
    package.__path__ = [str(ROOT)]
    sys.modules[PACKAGE] = package

    log_stub = types.ModuleType(f"{PACKAGE}.local_tools")

    class _Log:
        @staticmethod
        def warning(*a, **k):
            pass

        @staticmethod
        def debug(*a, **k):
            pass

    def current_user():
        if actor is None:
            raise RuntimeError("no request context")
        return actor

    rpc = rpc if rpc is not None else FakeRpc()
    if personal_projects:
        rpc.personal_projects = personal_projects

    log_stub.log = _Log
    log_stub.rpc_manager = rpc
    log_stub.current_user = current_user
    sys.modules[f"{PACKAGE}.local_tools"] = log_stub

    models = types.ModuleType(f"{PACKAGE}.models")
    models.__path__ = []
    pd = types.ModuleType(f"{PACKAGE}.models.pd")
    pd.__path__ = []
    registry_mod = types.ModuleType(f"{PACKAGE}.models.pd.registry")
    registry_mod.CONFIG_TYPE_REGISTRY = registry if registry is not None else {}
    registry_mod.ADMIN_MANAGED_TYPES = frozenset({"oauth_app"})
    sys.modules[f"{PACKAGE}.models"] = models
    sys.modules[f"{PACKAGE}.models.pd"] = pd
    sys.modules[f"{PACKAGE}.models.pd.registry"] = registry_mod

    spec = importlib.util.spec_from_file_location(
        f"{PACKAGE}.tracing_access", ROOT / "tracing_access.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def clear_stub_modules():
    yield
    for name in [n for n in sys.modules if n.startswith(PACKAGE)]:
        del sys.modules[name]


REF_A, REF_B = "{{secret.a}}", "{{secret.b}}"


@pytest.mark.parametrize("previous,new,changed", [
    ({}, {"refresh_token": REF_A}, True),            # login
    ({"refresh_token": REF_A}, {}, True),            # logout
    ({"refresh_token": REF_A}, {"refresh_token": REF_B}, True),  # login as someone else
    ({"refresh_token": REF_A}, {"refresh_token": REF_A, "scopes": ["x"]}, False),  # unrelated edit
    ({}, {"client_id": "c"}, False),
    (None, None, False),
])
def test_stored_login_change_detection(previous, new, changed):
    module = load_tracing_access()
    assert module.stored_login_changed(previous, new) is changed


def test_team_admin_may_manage_stored_login():
    module = load_tracing_access(rpc=FakeRpc(admin_project_users={25: {3}}))
    assert module.can_manage_stored_login(25, 3) is True


def test_team_editor_may_not_manage_stored_login():
    module = load_tracing_access(rpc=FakeRpc(admin_project_users={25: {3}}))
    assert module.can_manage_stored_login(25, 6) is False


def test_owner_may_manage_stored_login_in_own_personal_project():
    # Personal projects grant their owner editor+viewer only, so a plain admin check would lock them out.
    module = load_tracing_access(personal_projects={6: 40})
    assert module.can_manage_stored_login(40, 6) is True


def test_anonymous_never_manages_stored_login():
    module = load_tracing_access()
    assert module.can_manage_stored_login(25, None) is False
