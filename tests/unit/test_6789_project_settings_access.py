"""#6789: project_settings configurations are written by project admins and personal-project owners only."""

import pathlib
import sys
import types

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from test_6373_tracing_access import FakeRpc, clear_stub_modules, load_tracing_access  # noqa: F401


def _registry(**sections):
    return {name: types.SimpleNamespace(section=section, config_schema={}) for name, section in sections.items()}


REGISTRY = _registry(project_context="project_settings", project_chat_config="project_settings",
                     auto_routing="project_settings", llm_model="llm")


@pytest.mark.parametrize("config_type", ["project_context", "project_chat_config", "auto_routing"])
def test_project_settings_types_are_recognised(config_type):
    module = load_tracing_access(registry=REGISTRY)

    assert module.is_project_settings_type(config_type) is True


@pytest.mark.parametrize("config_type", ["llm_model", "unknown", None, ""])
def test_other_types_are_not_project_settings(config_type):
    module = load_tracing_access(registry=REGISTRY)

    assert module.is_project_settings_type(config_type) is False


def test_team_project_admin_may_manage_project_settings():
    module = load_tracing_access(rpc=FakeRpc(admin_project_users={7: {42}}))

    assert module.can_manage_project_settings(7, 42) is True


def test_team_project_editor_may_not_manage_project_settings():
    rpc = FakeRpc(admin_project_users={7: {42}})
    rpc.extra_roles[(7, 99)] = ["editor", "viewer"]
    module = load_tracing_access(rpc=rpc, personal_projects={99: 3})

    assert module.can_manage_project_settings(7, 99) is False


def test_private_project_owner_keeps_project_settings_without_the_admin_role():
    rpc = FakeRpc()
    rpc.extra_roles[(3, 99)] = ["editor", "viewer", "monitor"]
    module = load_tracing_access(rpc=rpc, personal_projects={99: 3})

    assert module.can_manage_project_settings(3, 99) is True


def test_role_lookup_failure_still_honours_private_project_ownership():
    module = load_tracing_access(rpc=FakeRpc(raises=True), personal_projects={99: 3})

    assert module.can_manage_project_settings(3, 99) is True
    assert module.can_manage_project_settings(7, 99) is False


def test_anonymous_caller_may_not_manage_project_settings():
    module = load_tracing_access()

    assert module.can_manage_project_settings(7, None) is False
