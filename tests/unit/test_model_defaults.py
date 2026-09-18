"""Default intent is separate from concrete model configuration and tier defaults."""
import ast
import importlib.util
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[2]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT/path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


defaults = load('default_intent', 'model_defaults.py')
Model = load('default_intent_model', 'models/pd/llm_model.py').SetDefaultModel
CONCRETE = {'default_llm_model_name': 'fixed', 'default_llm_model_project_id': 1,
            'default_llm_high_tier_model_name': 'high', 'default_llm_low_tier_model_name': 'low'}


def test_auto_retains_concrete_default_and_selecting_fixed_clears_intent():
    original = dict(CONCRETE)
    auto = defaults.update_default_secrets(original, Model(mode='auto'), {'enabled': True})
    assert original == CONCRETE
    assert auto == {**CONCRETE, 'default_llm_selection_mode': 'auto'}
    fixed = defaults.update_default_secrets(auto, Model(name='new', target_project_id=7), {})
    assert fixed == {**CONCRETE, 'default_llm_model_name': 'new', 'default_llm_model_project_id': 7}


@pytest.mark.parametrize('enabled', [False, None, 'true', 1])
def test_disabled_gates_suppress_intent_and_reject_new_auto_default(enabled):
    saved = {**CONCRETE, 'default_llm_selection_mode': 'auto'}
    assert defaults.effective_default_selection(saved, {'enabled': enabled}) is None
    assert saved['default_llm_model_name'] == 'fixed'
    with pytest.raises(ValueError, match='disabled'):
        defaults.update_default_secrets(saved, Model(mode='auto'), {'enabled': enabled})


def test_saved_intent_returns_only_typed_auto_binding():
    result = defaults.effective_default_selection({'default_llm_selection_mode': 'auto'}, {'enabled': True})
    assert result == {'mode': 'auto', 'profile_ref': {'id': 'v7-quality-cost', 'revision': 1},
                      'scope_mode': 'task_episode', 'reasoning': {'mode': 'auto'}}
    assert defaults.effective_default_selection(CONCRETE, {'enabled': True}) is None


@pytest.mark.parametrize('payload', [
    {'mode': 'auto', 'section': 'llm_high_tier'}, {'mode': 'auto', 'section': 'llm_low_tier'},
    {'mode': 'auto', 'section': 'embedding'}, {'mode': 'auto', 'name': 'fixed'},
    {'mode': 'auto', 'target_project_id': 1}, {}, {'name': 'fixed'}, {'mode': 'inherit'},
])
def test_invalid_default_requests_cannot_store_fake_or_tier_auto(payload):
    with pytest.raises(ValidationError): Model.model_validate(payload)


def test_updating_tier_does_not_change_project_auto_intent():
    saved = {**CONCRETE, 'default_llm_selection_mode': 'auto'}
    result = defaults.update_default_secrets(saved, Model(section='llm_low_tier', name='cheap', target_project_id=7), {})
    assert result['default_llm_selection_mode'] == 'auto'
    assert result['default_llm_model_name'] == 'fixed'
    assert result['default_llm_low_tier_model_name'] == 'cheap'


@pytest.mark.parametrize('surface,section,expect_auto', [
    ('chat', 'llm', True), ('agent', 'llm', True), ('pipeline', 'llm', False),
    ('pipeline_llm_node', 'llm', False), (None, 'llm', False), ('chat', 'embedding', False),
])
def test_actual_default_rpc_requires_explicit_creation_surface(monkeypatch, surface, section, expect_auto):
    # Compile the real method and stub only external owners. No Pylon import.
    path = ROOT/'rpc/getters.py'
    tree = ast.parse(path.read_text())
    method = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == 'configurations_get_default_models')
    method.decorator_list = []
    # Resolve its one relative helper import through this test's namespace.
    for node in ast.walk(method):
        if isinstance(node, ast.If):
            node.body = [n for n in node.body if not isinstance(n, ast.ImportFrom)]
    service = MagicMock()
    service.return_value.get_models.return_value = ({'default_model_name': 'fixed', 'default_model_project_id': 1}, 200)
    namespace = {'ModelConfigurationService': service,
        'get_default_selection': lambda p: {'mode': 'auto'}}
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(path), 'exec'), namespace)
    actual = namespace[method.name](None, 7, section=section, surface=surface)
    assert actual == ({'selection': {'mode': 'auto'}} if expect_auto else {'model_name': 'fixed', 'model_project_id': 1})
    assert service.called is not expect_auto


def test_enabling_auto_snapshots_inherited_concrete_and_reselection_keeps_it():
    inherited = {'default_model_name': 'shared-haiku', 'default_model_project_id': 1}
    selected = defaults.update_default_secrets({}, Model(mode='auto'), {'enabled': True}, inherited)
    assert selected == {'default_llm_selection_mode': 'auto',
        'default_llm_model_name': 'shared-haiku', 'default_llm_model_project_id': 1}
    changed_public = {'default_model_name': 'other', 'default_model_project_id': 1}
    assert defaults.update_default_secrets(selected, Model(mode='auto'), {'enabled': True}, changed_public) == selected


@pytest.mark.parametrize('concrete', [None, {}, {'default_model_name': 'incomplete'}])
def test_auto_requires_effective_concrete_binding_before_persistence(concrete):
    original = {}
    with pytest.raises(ValueError, match='concrete default'):
        defaults.update_default_secrets(original, Model(mode='auto'), {'enabled': True}, concrete)
    assert original == {}


def test_actual_post_snapshots_effective_default_before_vault_write():
    path = ROOT/'api/v2/models.py'
    fn = next(n for n in ast.walk(ast.parse(path.read_text())) if isinstance(n, ast.FunctionDef) and n.name == 'post')
    fn.decorator_list = []
    for node in ast.walk(fn):
        if isinstance(node, ast.Try):
            node.body = [n for n in node.body if not isinstance(n, ast.ImportFrom)]
    vault = MagicMock()
    vault.get_secrets.return_value = {}
    service = MagicMock()
    service.return_value.get_models.return_value = ({'default_model_name': 'inherited', 'default_model_project_id': 1}, 200)
    namespace = {'SetDefaultModel': Model, 'request': MagicMock(json={'mode': 'auto'}),
        'VaultClient': MagicMock(from_project=MagicMock(return_value=vault)),
        'get_effective_settings': lambda p: {'enabled': True},
        'update_default_secrets': defaults.update_default_secrets,
        'ModelConfigurationService': service, 'log': MagicMock()}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), str(path), 'exec'), namespace)
    assert namespace['post'](None, 7) == ({'result': 'success'}, 200)
    service.return_value.get_models.assert_called_once_with('llm', True)
    assert vault.set_secrets.call_args.args[0]['default_llm_model_name'] == 'inherited'
    vault.set_secrets.reset_mock()
    service.return_value.get_models.return_value = ({'default_model_name': None}, 200)
    assert namespace['post'](None, 7)[1] == 400
    vault.set_secrets.assert_not_called()
