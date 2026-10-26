"""The project_skills record: a strict boolean project switch that only project admins or personal owners may write (#7126)."""
import importlib.util
import pathlib
import sys
import types

import pytest
from pydantic import ValidationError

ROOT = pathlib.Path(__file__).resolve().parents[2]
PACKAGE = 'configurations_project_skills_test'


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
    loaded = {}
    for name in ['exceptions', 'models.pd.environment_settings', 'models.pd.auto_routing', 'models.pd.web_search_settings',
                 'models.pd.context_window_settings', 'models.pd.project_skills', 'routing_access']:
        spec = importlib.util.spec_from_file_location(PACKAGE + '.' + name, ROOT/(name.replace('.', '/') + '.py'))
        mod = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, spec.name, mod)
        spec.loader.exec_module(mod)
        loaded[name] = mod
    return types.SimpleNamespace(
        guard=loaded['routing_access'], model=loaded['models.pd.project_skills'].ProjectSkillsSettings,
        error=loaded['exceptions'].ConfigurationError,
    )


def _owner(modules, monkeypatch, project_id=7, actor=42):
    monkeypatch.setattr(modules.guard, 'current_actor_id', lambda: actor)
    monkeypatch.setattr(modules.guard, 'is_project_admin', lambda p, u: p == project_id and u == actor)


def test_off_by_default_and_strict(modules):
    assert modules.model().enabled is False
    assert modules.model(enabled=True).model_dump() == {'enabled': True}
    for value in ['true', 1, [], {}]:
        with pytest.raises(ValidationError):
            modules.model(enabled=value)
    with pytest.raises(ValidationError):
        modules.model(enabled=True, classifier=None)


def test_the_type_is_registered_as_a_project_setting():
    source = (ROOT / 'models/pd/registry.py').read_text()
    assert "type_name='project_skills'" in source
    assert source.index("type_name='project_skills'") < source.index("model=ProjectSkillsSettings")
    assert modules_metadata() == {'label': 'Project skills in chat', 'section': 'project_settings', 'type': 'project_skills'}


def modules_metadata():
    spec = importlib.util.spec_from_file_location('project_skills_model_7126', ROOT / 'models/pd/project_skills.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.ProjectSkillsSettings.model_config['json_schema_extra']['metadata']


def test_body_author_is_not_authority(modules):
    with pytest.raises(modules.error, match='project admins'):
        modules.guard.validate_routing_write('project_skills', 7, {'author_id': 42, 'data': {'enabled': True}})


@pytest.mark.parametrize('project_admin,personal_owner', [(True, False), (False, True)])
def test_project_admins_and_personal_owners_may_write(modules, monkeypatch, project_admin, personal_owner):
    monkeypatch.setattr(modules.guard, 'current_actor_id', lambda: 42)
    monkeypatch.setattr(modules.guard, 'is_project_admin', lambda p, u: p == 7 and u == 42 and project_admin)
    monkeypatch.setattr(modules.guard, 'is_own_personal_project', lambda p, u: p == 7 and u == 42 and personal_owner)
    result = modules.guard.validate_routing_write('project_skills', 7, {'data': {'enabled': True}})
    assert result['data'] == {'enabled': True}
    with pytest.raises(modules.error):
        modules.guard.validate_routing_write('project_skills', 8, {'data': {'enabled': True}})


def test_the_record_cannot_be_shared_or_renamed(modules, monkeypatch):
    _owner(modules, monkeypatch)
    for payload in [{'shared': True}, {'elitea_title': 'alternate_skills'}]:
        with pytest.raises(modules.error):
            modules.guard.validate_routing_write('project_skills', 7, payload)
    assert modules.guard.validate_routing_write('project_skills', 7, {'elitea_title': 'project_skills'}) == {'elitea_title': 'project_skills'}


def test_malformed_data_is_a_configuration_error(modules, monkeypatch):
    _owner(modules, monkeypatch)
    for data in [{'enabled': 'true'}, {'enabled': True, 'extra': 1}]:
        with pytest.raises(modules.error) as error:
            modules.guard.validate_routing_write('project_skills', 7, {'data': data})
        assert error.value.field == 'data'


def test_deleting_needs_an_owner_but_not_valid_data(modules, monkeypatch):
    with pytest.raises(modules.error):
        modules.guard.validate_routing_write('project_skills', 7, {}, deleting=True)
    _owner(modules, monkeypatch)
    assert modules.guard.validate_routing_write('project_skills', 7, {'data': {'stale': 1}}, deleting=True) == {'data': {'stale': 1}}


def test_the_payload_is_copied_not_mutated(modules, monkeypatch):
    _owner(modules, monkeypatch)
    payload = {'data': {'enabled': False}}
    result = modules.guard.validate_routing_write('project_skills', 7, payload)
    assert result is not payload and result == payload == {'data': {'enabled': False}}
