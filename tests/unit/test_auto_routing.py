"""Routing availability and privileged mutations, with no Pylon or database."""
import importlib.util
import pathlib
import sys
import types

import pytest
from pydantic import ValidationError

ROOT = pathlib.Path(__file__).resolve().parents[2]
PACKAGE = 'configurations_routing_test'


@pytest.fixture
def modules(monkeypatch):
    for suffix, path in [('', ROOT), ('.models', ROOT/'models'), ('.models.pd', ROOT/'models/pd')]:
        module = types.ModuleType(PACKAGE + suffix)
        module.__path__ = [str(path)]
        monkeypatch.setitem(sys.modules, module.__name__, module)
    common = types.ModuleType(PACKAGE + '.common_utils')
    common.get_public_project_id = lambda: 1
    access = types.ModuleType(PACKAGE + '.tracing_access')
    access.current_actor_id = lambda: None
    access.is_project_admin = lambda project, actor: False
    access.is_own_personal_project = lambda project, actor: False
    monkeypatch.setitem(sys.modules, common.__name__, common)
    monkeypatch.setitem(sys.modules, access.__name__, access)
    getters = types.ModuleType(PACKAGE + '.utils_getters')
    getters.get_project_configuration = lambda *a, **k: None
    monkeypatch.setitem(sys.modules, getters.__name__, getters)
    loaded = {}
    for name in ['exceptions', 'models.pd.environment_settings', 'models.pd.auto_routing', 'routing_access', 'routing_settings']:
        spec = importlib.util.spec_from_file_location(PACKAGE + '.' + name, ROOT/(name.replace('.', '/') + '.py'))
        mod = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, spec.name, mod)
        spec.loader.exec_module(mod)
        loaded[name] = mod
    return types.SimpleNamespace(
        guard=loaded['routing_access'], project=loaded['models.pd.auto_routing'],
        environment=loaded['models.pd.environment_settings'].EnvironmentSettings,
        error=loaded['exceptions'].ConfigurationError,
        settings=loaded['routing_settings'],
    )


def test_disabled_defaults(modules):
    settings = modules.environment().model_dump()
    assert settings['auto_routing_available'] is False
    assert settings['auto_routing_project_default'] is False
    assert modules.project.AutoRoutingProjectSettings().enabled is None


@pytest.mark.parametrize('master,default,override,expected', [
    (False, False, None, False), (False, True, True, False),
    (True, False, None, False), (True, False, True, True),
    (True, True, None, True), (True, True, False, False),
    ('true', True, True, False), (True, True, 'true', False),
])
def test_resolution(modules, master, default, override, expected):
    assert modules.project.routing_enabled({
        'auto_routing_available': master, 'auto_routing_project_default': default,
    }, {'enabled': override}) is expected


@pytest.mark.parametrize('value', ['true', 1, [], {}])
def test_project_flags_are_strict(modules, value):
    with pytest.raises(ValidationError):
        modules.project.AutoRoutingProjectSettings(enabled=value)


def test_disabled_environment_bootstrap_without_actor(modules):
    result = modules.guard.validate_routing_write('environment_settings', 1, {
        'elitea_title': 'environment_settings', 'data': {'system_sender_name': 'Elitea'},
    })
    assert result['data']['auto_routing_available'] is False


def test_body_author_is_not_authority(modules):
    with pytest.raises(modules.error, match='project admins'):
        modules.guard.validate_routing_write('auto_routing', 7, {
            'author_id': 42, 'data': {'enabled': True},
        })


@pytest.mark.parametrize('project_admin,personal_owner', [(True, False), (False, True)])
def test_project_control_owners(modules, monkeypatch, project_admin, personal_owner):
    monkeypatch.setattr(modules.guard, 'current_actor_id', lambda: 42)
    monkeypatch.setattr(modules.guard, 'is_project_admin', lambda p, u: p == 7 and u == 42 and project_admin)
    monkeypatch.setattr(modules.guard, 'is_own_personal_project', lambda p, u: p == 7 and u == 42 and personal_owner)
    result = modules.guard.validate_routing_write('auto_routing', 7, {'data': {'enabled': True}})
    assert result['data'] == {'enabled': True}
    with pytest.raises(modules.error):
        modules.guard.validate_routing_write('auto_routing', 8, {'data': {'enabled': True}})


def test_project_settings_cannot_be_shared_or_renamed(modules):
    for payload in [{'shared': True}, {'elitea_title': 'alternate_auto'}]:
        with pytest.raises(modules.error):
            modules.guard.validate_routing_write('auto_routing', 7, payload)


def test_platform_enable_requires_administration_admin(modules, monkeypatch):
    payload = {'elitea_title': 'environment_settings', 'data': {'auto_routing_available': True}}
    with pytest.raises(modules.error):
        modules.guard.validate_routing_write('environment_settings', 1, payload)
    monkeypatch.setattr(modules.guard, 'current_actor_id', lambda: 42)
    monkeypatch.setattr(modules.guard, 'is_platform_admin', lambda actor: actor == 42)
    with pytest.raises(modules.error):
        modules.guard.validate_routing_write('environment_settings', 7, payload)
    assert modules.guard.validate_routing_write('environment_settings', 1, payload)['data']['auto_routing_available']


def test_legacy_settings_update_preserves_flags_without_actor(modules):
    old = {'elitea_title': 'environment_settings', 'data': {
        'auto_routing_available': True, 'auto_routing_project_default': True,
    }}
    payload = {'data': {'system_sender_name': 'Updated'}}
    result = modules.guard.validate_routing_write('environment_settings', 1, payload, existing=old)
    assert result['data']['auto_routing_available'] is True
    assert result['data']['auto_routing_project_default'] is True
    assert payload == {'data': {'system_sender_name': 'Updated'}}


def test_rpc_without_actor_cannot_disable_or_delete_active_flags(modules):
    old = {'elitea_title': 'environment_settings', 'data': {'auto_routing_available': True}}
    for payload, deleting in [({'data': {'auto_routing_available': False}}, False), ({}, True)]:
        with pytest.raises(modules.error):
            modules.guard.validate_routing_write('environment_settings', 1, payload, existing=old, deleting=deleting)


def test_malformed_environment_flag_has_configuration_error(modules):
    with pytest.raises(modules.error):
        modules.guard.validate_routing_write('environment_settings', 1, {'data': {'auto_routing_available': 'false'}})


def test_unrelated_config_does_not_require_routing_authority(modules):
    payload = {'data': {'key': 'value'}}
    assert modules.guard.validate_routing_write('github', 7, payload) is payload


def test_current_settings_read_uses_platform_and_exact_project_record(modules, monkeypatch):
    calls = []
    records = {1: {'data': {'auto_routing_available': True, 'auto_routing_project_default': False}},
               7: {'data': {'enabled': True}}}
    def read(project, filters):
        calls.append((project, filters))
        return records[project]
    monkeypatch.setattr(modules.settings, 'get_project_configuration', read)
    before = modules.settings.get_effective_settings(7)
    assert before['enabled'] is True
    assert calls == [(1, {'type': 'environment_settings', 'elitea_title': 'environment_settings'}),
                     (7, {'type': 'auto_routing', 'elitea_title': 'auto_routing'})]
    records[1]['data']['auto_routing_available'] = False
    after = modules.settings.get_effective_settings(7)
    assert after['enabled'] is False and after['revision'] != before['revision']


@pytest.mark.parametrize('environment,project', [
    (None, None), ({'data': []}, {'data': {'enabled': True}}),
    ({'data': {'auto_routing_available': 'true'}}, {'data': {'enabled': True}}),
    ({'data': {'auto_routing_available': True, 'auto_routing_project_default': True}}, {'data': {'enabled': 'true'}}),
])
def test_missing_or_malformed_availability_cannot_enable(modules, environment, project):
    assert modules.settings.effective_settings(environment, project)['enabled'] is False


def test_public_project_admin_is_not_platform_admin(modules, monkeypatch):
    monkeypatch.setattr(modules.guard, 'current_actor_id', lambda: 42)
    monkeypatch.setattr(modules.guard, 'is_project_admin', lambda *a: True)
    monkeypatch.setattr(modules.guard, 'is_platform_admin', lambda *a: False)
    with pytest.raises(modules.error, match='Administration admins'):
        modules.guard.validate_routing_write('environment_settings', 1, {
            'elitea_title': 'environment_settings', 'data': {'auto_routing_available': True}})


def test_administration_method_preserves_environment_and_requires_actual_admin():
    import ast
    source = ROOT/'methods/routing.py'
    tree = ast.parse(source.read_text())
    tree.body = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.ClassDef))]
    record = {'id': 9, 'data': {'system_sender_name': 'Company', 'auto_routing_available': False}}
    writes = []
    namespace = {'web': types.SimpleNamespace(method=lambda: lambda f: f),
        'current_actor_id': lambda: 42, 'is_platform_admin': lambda actor: actor == 42,
        'get_public_project_id': lambda: 1, 'get_project_configuration': lambda *a: record,
        'update_configuration': lambda project, ident, data: writes.append((project, ident, data)) or {'id': ident, **data}}
    exec(compile(tree, str(source), 'exec'), namespace)
    method = namespace['Method']()
    actual = method.auto_routing_platform_settings({'available': True, 'project_default': False})
    assert actual == {'available': True, 'project_default': False, 'can_manage': True}
    assert writes[0] == (1, 9, {'data': {'system_sender_name': 'Company', 'auto_routing_available': True, 'auto_routing_project_default': False}})
    namespace['is_platform_admin'] = lambda *a: False
    with pytest.raises(PermissionError):
        method.auto_routing_platform_settings({'available': False, 'project_default': False})
    assert len(writes) == 1


def test_administration_method_rejects_partial_or_string_flags():
    import ast
    source = ROOT/'methods/routing.py';tree = ast.parse(source.read_text())
    tree.body = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.ClassDef))]
    namespace = {'web': types.SimpleNamespace(method=lambda: lambda f: f),
        'current_actor_id': lambda: 42, 'is_platform_admin': lambda actor: True,
        'get_public_project_id': lambda: 1, 'get_project_configuration': lambda *a: None}
    exec(compile(tree, str(source), 'exec'), namespace)
    for bad in ({'available': True}, {'available': 'true', 'project_default': False}, {'available': True, 'project_default': False, 'author_id': 42}):
        with pytest.raises(ValueError):
            namespace['Method']().auto_routing_platform_settings(bad)
