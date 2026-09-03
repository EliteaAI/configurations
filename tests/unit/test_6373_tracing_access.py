"""Tracing credentials are restricted to project admins and personal-project owners."""

import importlib.util
import pathlib
import sys
import types

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]

PACKAGE = "configurations_6373"


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


def test_langfuse_is_tracing_even_when_registry_is_empty():
    """Categories reach the registry via the indexer event; a categories-only test fails open."""
    module = load_tracing_access(registry={})

    assert module.is_tracing_type("langfuse") is True


def test_registry_categories_mark_a_type_as_tracing():
    module = load_tracing_access(registry={
        "otel": FakeRegistryEntry({"metadata": {"categories": ["Tracing"]}}),
    })

    assert module.is_tracing_type("otel") is True


def test_unrelated_types_are_not_tracing():
    module = load_tracing_access(registry={
        "github": FakeRegistryEntry({"metadata": {"categories": ["vcs"]}}),
    })

    assert module.is_tracing_type("github") is False
    assert module.is_tracing_type(None) is False


def test_project_admin_may_manage_tracing():
    module = load_tracing_access(rpc=FakeRpc(admin_project_users={7: {42}}))

    assert module.can_manage_tracing(7, 42) is True


def test_non_admin_may_not_manage_tracing_in_a_team_project():
    module = load_tracing_access(
        rpc=FakeRpc(admin_project_users={7: {42}}),
        personal_projects={99: 3},
    )

    assert module.can_manage_tracing(7, 99) is False


def test_personal_project_owner_may_manage_tracing_without_the_admin_role():
    """Personal projects grant their owner editor+viewer only, never admin."""
    module = load_tracing_access(
        rpc=FakeRpc(admin_project_users={}),
        personal_projects={99: 3},
    )

    assert module.can_manage_tracing(3, 99) is True


def test_another_users_personal_project_is_not_manageable():
    module = load_tracing_access(
        rpc=FakeRpc(admin_project_users={}),
        personal_projects={99: 3, 100: 4},
    )

    assert module.can_manage_tracing(4, 99) is False


def test_anonymous_caller_may_not_manage_tracing():
    module = load_tracing_access(rpc=FakeRpc(admin_project_users={7: {42}}))

    assert module.can_manage_tracing(7, None) is False


def test_a_role_merely_containing_admin_is_not_an_admin():
    """admin_check_user_is_admin substring-matches, so project roles like billing-admin pass it."""
    rpc = FakeRpc(admin_project_users={})
    rpc.extra_roles = {(7, 99): ["billing-admin", "editor"]}
    module = load_tracing_access(rpc=rpc)

    assert module.can_manage_tracing(7, 99) is False


def test_role_lookup_failure_fails_closed_in_a_team_project():
    module = load_tracing_access(rpc=FakeRpc(raises=True), personal_projects={99: 3})

    assert module.can_manage_tracing(7, 99) is False


def test_role_lookup_failure_still_honours_personal_project_ownership():
    """The two ownership signals are independent; an admin-RPC blip must not revoke the owner."""
    module = load_tracing_access(rpc=FakeRpc(raises=True), personal_projects={99: 3})

    assert module.can_manage_tracing(3, 99) is True


def test_hidden_types_are_empty_for_a_manager_and_populated_otherwise():
    module = load_tracing_access(
        registry={"otel": FakeRegistryEntry({"metadata": {"categories": ["tracing"]}})},
        rpc=FakeRpc(admin_project_users={7: {42}}),
        personal_projects={99: 3},
    )

    assert module.hidden_tracing_types(7, 42) == set()
    assert module.hidden_tracing_types(7, 99) == {"langfuse", "otel"}


def test_every_role_lookup_is_bounded_by_a_timeout():
    """These lookups sit in the request path of a credential write; an untimed RPC hangs it."""
    rpc = FakeRpc(personal_projects={99: 3})
    module = load_tracing_access(rpc=rpc)

    module.can_manage_tracing(7, 99)

    assert len(rpc.timeouts) == 2, "both the admin and personal-project lookups must be timed"
    assert all(seconds and seconds > 0 for seconds in rpc.timeouts)


def test_actor_is_none_outside_a_request_context():
    """The unauthenticated catalog endpoints must degrade to 'anonymous', not raise."""
    module = load_tracing_access(actor=None)

    assert module.current_actor_id() is None


def test_actor_is_read_from_the_current_user():
    module = load_tracing_access(actor={"id": 42})

    assert module.current_actor_id() == 42
