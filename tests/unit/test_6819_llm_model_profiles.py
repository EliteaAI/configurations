"""#6819: reasoning profiles recognized from the model name, the fields that store the
result and the save-time bounds.

The profile list is the contract the admin form renders; the recognition table below is
the local model inventory (p_1 / p_2 on 2026-09-29) plus the families the docs name.
"""
import importlib
import importlib.util
import pathlib
import re
import sys
import types
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError
from sqlalchemy import Boolean, Integer, String
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

ROOT = pathlib.Path(__file__).resolve().parents[2]
PACKAGE = 'configurations_6819'
CREDS = {'elitea_title': 'creds', 'private': False}


def _load_llm_model():
    spec = importlib.util.spec_from_file_location('llm_model_6819', ROOT / 'models/pd/llm_model.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope='module')
def profiles():
    package = types.ModuleType(PACKAGE)
    package.__path__ = [str(ROOT)]
    sys.modules[PACKAGE] = package
    module = importlib.import_module(f'{PACKAGE}.llm_model_profiles')
    yield module
    for name in [m for m in sys.modules if m.startswith(PACKAGE)]:
        del sys.modules[name]


@pytest.fixture(scope='module')
def LlmModel():
    return _load_llm_model().LlmModel


@pytest.fixture(scope='module')
def LlmModelList():
    return _load_llm_model().LlmModelList


def _data(**overrides):
    return {'name': 'gpt-5.4', 'ai_credentials': CREDS, 'supports_reasoning': True, **overrides}


def _errors(exc_info):
    return {'.'.join(str(part) for part in error['loc']): error['msg'] for error in exc_info.value.errors()}


# --- recognition -------------------------------------------------------------------------

RECOGNITION = [
    ('claude-fable-5-1', 'anthropic-fable-mythos'),
    ('anthropic.claude-mythos-5', 'anthropic-fable-mythos'),
    ('eu.anthropic.claude-opus-5-5', 'anthropic-opus-5-5'),
    ('claude-opus-5.5', 'anthropic-opus-5-5'),
    ('eu.anthropic.claude-opus-5', 'anthropic-adaptive'),
    ('global.anthropic.claude-sonnet-5', 'anthropic-adaptive'),
    ('anthropic.claude-sonnet-5', 'anthropic-adaptive'),
    ('claude_sonnet_5', 'anthropic-adaptive'),
    ('eu.anthropic.claude-opus-4-7', 'anthropic-adaptive'),
    ('eu.anthropic.claude-opus-4-8', 'anthropic-adaptive'),
    ('claude-opus-4.7', 'anthropic-adaptive'),
    ('claude-sonnet-4-6', 'anthropic-4-6'),
    ('eu.anthropic.claude-sonnet-4-6', 'anthropic-4-6'),
    ('eu.anthropic.claude-opus-4-6-v1', 'anthropic-4-6'),
    ('claude-sonnet-4-5', 'anthropic-legacy-budget'),
    ('eu.anthropic.claude-sonnet-4-5-20250929-v1:0', 'anthropic-legacy-budget'),
    ('eu.anthropic.claude-opus-4-5-20251101-v1:0', 'anthropic-legacy-budget'),
    ('eu.anthropic.claude-haiku-4-5-20251001-v1:0', 'anthropic-legacy-budget'),
    ('claude-sonnet-4-20250514', 'anthropic-legacy-budget'),
    ('claude-opus-4-1', 'anthropic-legacy-budget'),
    ('claude-3-7-sonnet', 'anthropic-legacy-budget'),
    ('claude-3.7-sonnet', 'anthropic-legacy-budget'),
    ('gpt-5-pro', 'openai-gpt-5-pro'),
    ('gpt-5.5-pro', 'openai-gpt-5-pro'),
    ('gpt-6-astra', 'openai-gpt-6-astra'),
    ('global.openai.gpt-6-sol', 'openai-gpt-6'),
    ('gpt-6-luna', 'openai-gpt-6'),
    ('global.openai.gpt-5.6-luna', 'openai-gpt-5-6'),
    ('global.openai.gpt-5.6-sol', 'openai-gpt-5-6'),
    ('global.openai.gpt-5.6-terra', 'openai-gpt-5-6'),
    ('gpt-5.6-luna', 'openai-gpt-5-6'),
    ('GPT-5.6', 'openai-gpt-5-6'),
    ('gpt-5.5', 'openai-gpt-5-5'),
    ('gpt-5.4', 'openai-gpt-5-4'),
    ('gpt-5.4-mini', 'openai-gpt-5-4'),
    ('gpt-5.4-2026-03-05', 'openai-gpt-5-4'),
    ('gpt-5.1-codex-max', 'openai-gpt-5-1-codex-max'),
    ('gpt-5.1-codex', 'openai-gpt-5-1-codex'),
    ('azure/gpt-5.1-codex', 'openai-gpt-5-1-codex'),
    ('gpt-5.1-codex-2025-11-13', 'openai-gpt-5-1-codex'),
    ('gpt-5.1-codex-mini', 'openai-gpt-5-1-codex-mini'),
    ('gpt-5.2-codex', 'openai-gpt-5-2-5-1'),
    ('gpt-5-codex', 'openai-gpt-5'),
    ('gpt-5.1', 'openai-gpt-5-2-5-1'),
    ('gpt-5.2', 'openai-gpt-5-2-5-1'),
    ('gpt-5', 'openai-gpt-5'),
    ('gpt-5-mini', 'openai-gpt-5'),
    ('gpt-5-nano', 'openai-gpt-5'),
    ('gpt-5-2025-08-07', 'openai-gpt-5'),
    ('gpt-5-10', 'openai-gpt-5'),
    ('gpt-5-chat-latest', 'openai-gpt-5-chat'),
    ('gpt-5.1-chat-latest', 'openai-gpt-5-x-chat'),
    ('gpt-5.2-chat-latest', 'openai-gpt-5-x-chat'),
    ('claude-3-haiku-20240307', None),
    ('claude-3-5-haiku-20241022', None),
    ('claude-haiku-4-5', 'anthropic-legacy-budget'),
    ('gpt-4.1', 'openai-gpt-4'),
    ('gpt-4o-2024-11-20', 'openai-gpt-4'),
    ('gpt-4-azure', 'openai-gpt-4'),
    ('global.xai.grok-4.6', None),
    ('model-router', None),
    ('o3-mini', None),
    ('gemini-2.5-pro', None),
    ('claude-3-5-sonnet', None),
    ('', None),
    (None, None),
]


@pytest.mark.parametrize('model_name,profile_id', RECOGNITION)
def test_model_names_are_recognized_with_provider_prefixes(profiles, model_name, profile_id):
    profile = profiles.recognize_profile(model_name)
    assert (profile['id'] if profile else None) == profile_id


def test_every_match_token_reaches_its_own_profile(profiles):
    # first match wins, so a token shadowed by an earlier profile would be dead data
    for profile in profiles.PROFILES:
        for token in profile['match']:
            assert profiles.recognize_profile(token)['id'] == profile['id'], token


def test_gpt_5_1_codex_offers_neither_none_nor_minimal(profiles):
    # #6908: gpt-5.1-codex answers none and minimal with "Supported values are: 'low', 'medium', and 'high'"
    assert list(profiles.recognize_profile('gpt-5.1-codex')['supported_efforts']) == ['low', 'medium', 'high']


def test_unprobed_codex_names_keep_the_levels_they_had(profiles):
    # only gpt-5.1-codex is proven; the others must resolve exactly as before #6908
    assert list(profiles.recognize_profile('gpt-5.1-codex-mini')['supported_efforts']) == ['none', 'low', 'medium', 'high']
    assert list(profiles.recognize_profile('gpt-5.1-codex-max')['supported_efforts']) == [
        'none', 'low', 'medium', 'high', 'xhigh']


def test_profile_ids_are_unique_and_tokens_are_normalized(profiles):
    ids = [profile['id'] for profile in profiles.PROFILES]
    assert len(ids) == len(set(ids))
    for profile in profiles.PROFILES:
        for token in profile['match']:
            assert token == profiles.normalize_model_name(token), token


# --- payload -----------------------------------------------------------------------------

def test_payload_carries_matching_rule_levels_and_platform_flag(profiles):
    payload = profiles.profiles_payload()
    assert payload['version'] == profiles.PROFILE_LIST_VERSION
    assert payload['matching'] == {'normalize': {'lowercase': True, 'replace': {'.': '-', '_': '-'}},
                                   'strategy': 'first_profile_with_any_token_as_substring_not_followed_by_digit'}
    assert payload['effort_levels'] == ['none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max']
    assert payload['thinking_types'] == ['adaptive', 'enabled', 'always_on']
    assert payload['platform'] == {'claude_off_path_implemented': False}
    assert [profile['id'] for profile in payload['profiles']] == [profile['id'] for profile in profiles.PROFILES]


def test_every_reasoning_profile_is_internally_valid(profiles, LlmModel):
    for profile in profiles.profiles_payload()['profiles']:
        assert isinstance(profile['match'], list) and isinstance(profile['notes'], list)
        if not profile['supports_reasoning']:
            assert profile['supported_efforts'] == [] and profile['default_effort'] is None
            assert profile['thinking_type'] is None and not profile['locked']
            continue
        LlmModel.model_validate(_data(name=profile['match'][0], thinking_type=profile['thinking_type'],
                                      supported_efforts=profile['supported_efforts'],
                                      default_effort=profile['default_effort']))
        profiles.check_llm_model_profile_bounds('llm_model', _data(
            name=profile['match'][0], thinking_type=profile['thinking_type'],
            supported_efforts=profile['supported_efforts'], default_effort=profile['default_effort']))


@pytest.mark.parametrize('profile_id,lock_reason', [
    ('anthropic-fable-mythos', 'provider'),
    ('anthropic-opus-5-5', 'provider'),
    ('anthropic-adaptive', 'platform'),
    ('anthropic-4-6', 'platform'),
    ('anthropic-legacy-budget', None),
    ('openai-gpt-5-pro', 'provider'),
    ('openai-gpt-6-astra', 'provider'),
    ('openai-gpt-6', None),
    ('openai-gpt-5-6', None),
    ('openai-gpt-5', None),
    ('openai-gpt-5-chat', None),
    ('openai-gpt-5-x-chat', None),
    ('openai-gpt-4', None),
])
def test_lock_reason_per_profile(profiles, profile_id, lock_reason):
    profile = next(p for p in profiles.profiles_payload()['profiles'] if p['id'] == profile_id)
    assert profile['lock_reason'] == lock_reason
    assert profile['locked'] is (lock_reason is not None)


def test_platform_lock_disappears_when_the_claude_off_path_ships(profiles, monkeypatch):
    monkeypatch.setattr(profiles, 'CLAUDE_OFF_PATH_IMPLEMENTED', True)
    reasons = {p['id']: p['lock_reason'] for p in profiles.profiles_payload()['profiles']}
    assert reasons['anthropic-adaptive'] is None and reasons['anthropic-4-6'] is None
    assert reasons['anthropic-fable-mythos'] == 'provider'


@pytest.mark.parametrize('profile_id,thinking_type,efforts,default', [
    ('anthropic-fable-mythos', 'always_on', ['low', 'medium', 'high', 'xhigh', 'max'], 'medium'),
    ('anthropic-opus-5-5', 'always_on', ['low', 'medium', 'high', 'xhigh', 'max'], 'medium'),
    ('anthropic-adaptive', 'adaptive', ['low', 'medium', 'high', 'xhigh', 'max'], 'medium'),
    ('anthropic-4-6', 'adaptive', ['low', 'medium', 'high', 'max'], 'medium'),
    ('anthropic-legacy-budget', 'enabled', ['low', 'medium', 'high'], 'medium'),
    ('openai-gpt-5-pro', None, ['high'], 'high'),
    ('openai-gpt-6-astra', None, ['low', 'medium', 'high', 'xhigh', 'max'], 'medium'),
    ('openai-gpt-6', None, ['none', 'low', 'medium', 'high', 'xhigh', 'max'], 'medium'),
    ('openai-gpt-5-6', None, ['none', 'low', 'medium', 'high', 'xhigh'], 'medium'),
    ('openai-gpt-5-5', None, ['none', 'low', 'medium', 'high', 'xhigh'], 'medium'),
    ('openai-gpt-5-4', None, ['none', 'low', 'medium', 'high', 'xhigh'], 'medium'),
    ('openai-gpt-5-1-codex-max', None, ['none', 'low', 'medium', 'high', 'xhigh'], 'medium'),
    ('openai-gpt-5-1-codex-mini', None, ['none', 'low', 'medium', 'high'], 'medium'),
    ('openai-gpt-5-1-codex', None, ['low', 'medium', 'high'], 'medium'),
    ('openai-gpt-5-2-5-1', None, ['none', 'low', 'medium', 'high'], 'medium'),
    ('openai-gpt-5-x-chat', None, ['medium'], 'medium'),
    ('openai-gpt-5', None, ['minimal', 'low', 'medium', 'high'], 'medium'),
])
def test_profile_values_match_the_decided_table(profiles, profile_id, thinking_type, efforts, default):
    profile = next(p for p in profiles.profiles_payload()['profiles'] if p['id'] == profile_id)
    assert profile['supports_reasoning'] is True
    assert (profile['thinking_type'], profile['supported_efforts'], profile['default_effort']) == (
        thinking_type, efforts, default)


# --- save-time bounds ---------------------------------------------------------------------

def _bound_error(profiles, data):
    with pytest.raises(profiles.ConfigurationError) as caught:
        profiles.check_llm_model_profile_bounds('llm_model', data)
    return caught.value


def test_codex_row_offering_none_is_rejected_on_save(profiles):
    error = _bound_error(profiles, _data(name='gpt-5.1-codex', supported_efforts=['none', 'low', 'medium', 'high'],
                                         default_effort='medium'))
    assert error.field == 'supported_efforts' and 'none' in error.message


def test_no_reasoning_profile_rejects_supports_reasoning(profiles):
    error = _bound_error(profiles, _data(name='gpt-4-azure', supports_reasoning=True))
    assert error.field == 'supports_reasoning' and 'OpenAI GPT-4 family' in error.message
    error = _bound_error(profiles, _data(name='gpt-5-chat-latest', supports_reasoning=True))
    assert 'OpenAI GPT-5 chat' in error.message
    profiles.check_llm_model_profile_bounds('llm_model', _data(name='gpt-4.1', supports_reasoning=False))


def test_recognized_profile_rejects_another_thinking_type(profiles):
    error = _bound_error(profiles, _data(name='eu.anthropic.claude-opus-4-7', thinking_type='enabled'))
    assert error.field == 'thinking_type' and 'adaptive, not enabled' in error.message
    error = _bound_error(profiles, _data(name='claude-fable-5-1', thinking_type='adaptive'))
    assert 'always_on, not adaptive' in error.message
    error = _bound_error(profiles, _data(name='gpt-5.6-luna', thinking_type='adaptive'))
    assert 'no thinking mode' in error.message


def test_recognized_profile_rejects_levels_it_does_not_support(profiles):
    error = _bound_error(profiles, _data(name='claude-sonnet-4-6', supported_efforts=['low', 'xhigh', 'max']))
    assert error.field == 'supported_efforts' and error.message.endswith(': xhigh')
    error = _bound_error(profiles, _data(name='global.openai.gpt-5.6-luna', supported_efforts=['medium', 'max']))
    assert 'OpenAI GPT-5.6' in error.message and error.message.endswith(': max')
    error = _bound_error(profiles, _data(name='claude-fable-5-1', supported_efforts=['none', 'high']))
    assert error.message.endswith(': none')
    error = _bound_error(profiles, _data(name='gpt-5.1-chat-latest', supported_efforts=['none', 'medium', 'high']))
    assert 'GPT-5.1 / GPT-5.2 chat' in error.message and error.message.endswith(': none, high')


@pytest.mark.parametrize('data', [
    _data(name='global.xai.grok-4.6', thinking_type='enabled', supported_efforts=['none', 'max'], default_effort='max'),
    _data(name='model-router', supports_reasoning=True),
    _data(name='eu.anthropic.claude-opus-4-7'),
    _data(name='eu.anthropic.claude-opus-4-7', thinking_type=None, supported_efforts=None, default_effort=None),
    _data(name='claude-sonnet-4-6', thinking_type='adaptive', supported_efforts=['low', 'max'], default_effort='max'),
    _data(name='claude-fable-5-1', supports_reasoning=False),
])
def test_unrecognized_null_and_profile_consistent_rows_pass(profiles, data):
    profiles.check_llm_model_profile_bounds('llm_model', data)


def test_only_llm_model_rows_are_bounded(profiles):
    profiles.check_llm_model_profile_bounds('embedding_model', _data(name='gpt-4-azure', supports_reasoning=True))
    profiles.check_llm_model_profile_bounds('llm_model', None)


# --- schema ------------------------------------------------------------------------------

def test_fields_default_to_null_and_are_dumped(LlmModel):
    dumped = LlmModel.model_validate({'name': 'gpt-5.4', 'ai_credentials': CREDS}).model_dump()
    assert (dumped['thinking_type'], dumped['supported_efforts'], dumped['default_effort']) == (None, None, None)


@pytest.mark.parametrize('field,value', [
    ('thinking_type', 'adaptive'), ('supported_efforts', ['low']), ('default_effort', 'low'),
])
def test_fields_require_supports_reasoning(LlmModel, field, value):
    with pytest.raises(ValidationError) as caught:
        LlmModel.model_validate(_data(supports_reasoning=False, **{field: value}))
    assert field in _errors(caught)


def test_valid_combinations_are_stored_in_order_without_duplicates(LlmModel):
    model = LlmModel.model_validate(_data(thinking_type='always_on', supported_efforts=['high', 'low', 'high', 'max'],
                                          default_effort='high'))
    assert model.supported_efforts == ['high', 'low', 'max'] and model.default_effort == 'high'
    model = LlmModel.model_validate(_data(supported_efforts=['none', 'minimal', 'low'], default_effort='low'))
    assert model.thinking_type is None and model.supported_efforts == ['none', 'minimal', 'low']


@pytest.mark.parametrize('overrides,field,fragment', [
    ({'supported_efforts': []}, 'supported_efforts', 'at least one'),
    ({'thinking_type': 'enabled', 'supported_efforts': ['low', 'xhigh'], 'default_effort': 'low'},
     'supported_efforts', 'token budget'),
    ({'thinking_type': 'always_on', 'supported_efforts': ['none', 'low'], 'default_effort': 'low'},
     'supported_efforts', "'always_on' cannot offer 'none'"),
    ({'supported_efforts': ['none', 'low'], 'default_effort': 'none'}, 'default_effort', "cannot be 'none'"),
    ({'supported_efforts': ['low', 'medium'], 'default_effort': 'high'}, 'default_effort', 'must be one of'),
    ({'supported_efforts': ['low', 'medium']}, 'default_effort', 'required when supported_efforts'),
    ({'default_effort': 'medium'}, 'default_effort', 'requires supported_efforts'),
    ({'supported_efforts': ['ultra']}, 'supported_efforts.0', 'Input should be'),
    ({'thinking_type': 'disabled'}, 'thinking_type', 'Input should be'),
])
def test_inconsistent_combinations_are_rejected_on_the_field(LlmModel, overrides, field, fragment):
    with pytest.raises(ValidationError) as caught:
        LlmModel.model_validate(_data(**overrides))
    errors = _errors(caught)
    assert field in errors and fragment in errors[field], errors


# --- read shape --------------------------------------------------------------------------

def test_model_list_carries_the_fields_and_defaults_to_null(LlmModelList):
    row = types.SimpleNamespace(name='claude-fable-5-1', display_name='Fable', project_id=1, thinking_type='always_on',
                                supported_efforts=['low', 'high'], default_effort='high')
    item = LlmModelList.model_validate(row).model_dump(mode='json')
    assert (item['thinking_type'], item['supported_efforts'], item['default_effort']) == ('always_on', ['low', 'high'], 'high')
    bare = LlmModelList.model_validate(types.SimpleNamespace(name='gpt-5.4', display_name='GPT', project_id=1))
    assert (bare.thinking_type, bare.supported_efforts, bare.default_effort) == (None, None, None)


class _Base(DeclarativeBase):
    pass


class _Configuration(_Base):
    __tablename__ = 'configuration'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(Integer)
    label: Mapped[str] = mapped_column(String)
    elitea_title: Mapped[str] = mapped_column(String)
    type: Mapped[str] = mapped_column(String)
    section: Mapped[str] = mapped_column(String)
    data: Mapped[dict] = mapped_column(JSONB)
    shared: Mapped[bool] = mapped_column(Boolean)
    status_ok: Mapped[bool] = mapped_column(Boolean)
    author_id: Mapped[int] = mapped_column(Integer)
    updated_at: Mapped[str] = mapped_column(String, nullable=True)


def _stub(name, **attrs):
    module = types.ModuleType(f'{PACKAGE}_utils.{name}')
    module.__dict__.update(attrs)
    return module


@pytest.fixture
def utils_module():
    package = PACKAGE + '_utils'
    for suffix, path in [('', ROOT), ('.models', ROOT / 'models'), ('.models.pd', ROOT / 'models/pd')]:
        module = types.ModuleType(package + suffix)
        module.__path__ = [str(path)]
        sys.modules[module.__name__] = module
    llm_model = importlib.import_module(f'{package}.models.pd.llm_model')
    stubs = {
        'common_utils': _stub('common_utils', get_personal_project_id=None, get_public_project_id=None),
        'local_tools': _stub('local_tools', db=MagicMock(), store_secrets=MagicMock(), purge_secrets=None,
                             event_manager=MagicMock(), log=MagicMock(), VaultClient=None),
        'models.configuration': _stub('models.configuration', Configuration=_Configuration),
        'models.pd.configuration': _stub('models.pd.configuration', ConfigurationCreate=None,
                                         ConfigurationDetails=MagicMock(), ConfigurationCreateRpc=None,
                                         ConfigurationList=None),
        'models.pd.registry': _stub('models.pd.registry', CONFIG_TYPE_REGISTRY={
            'llm_model': types.SimpleNamespace(model=llm_model.LlmModel, config_schema=llm_model.LlmModel.model_json_schema()),
        }),
        'folder_access': _stub('folder_access', folder_exclusion_clause=None),
        'routing_access': _stub('routing_access', validate_routing_write=lambda type_, project, payload, existing=None: payload),
    }
    for name, module in stubs.items():
        sys.modules[f'{package}.{name}'] = module
    yield importlib.import_module(f'{package}.utils')
    for name in [m for m in sys.modules if m.startswith(package)]:
        del sys.modules[name]


def test_models_listing_selects_the_three_fields_from_the_row_data(utils_module):
    query = utils_module.get_configuration_llm_models_with_limits_query(Session(), 1, [])
    columns = [column['name'] for column in query.column_descriptions]
    assert {'thinking_type', 'supported_efforts', 'default_effort'} <= set(columns)
    compiled = query.statement.compile(dialect=postgresql.dialect())
    # JSONB subscripting renders as `data -> key` (SQLAlchemy 2.0.35), `data[key]` (2.0.45) or
    # `data[key::TEXT]` (2.1); `.astext` renders as `->>` with the same optional cast
    for column, operator in [('thinking_type', '->>'), ('supported_efforts', r'(?:->|\[)'), ('default_effort', '->>')]:
        selected = re.search(rf"configuration\.data ?{operator} ?%\((\w+)\)s(?:::TEXT)?\]? AS {column}", str(compiled))
        assert selected and compiled.params[selected.group(1)] == column, (column, str(compiled))


def test_update_re_applies_the_profile_bound_after_schema_validation(utils_module):
    utils_module.db.get_session.return_value.__enter__.return_value.query.return_value.filter_by.return_value.first.return_value = \
        _Configuration(id=7, project_id=1, type='llm_model', elitea_title='fable', author_id=3, data=_data(),
                       shared=False, status_ok=True)
    with pytest.raises(utils_module.ConfigurationError) as caught:
        utils_module.update_configuration(1, 7, {'data': _data(name='claude-fable-5-1', thinking_type='adaptive',
                                                               supported_efforts=['low'], default_effort='low')})
    assert caught.value.field == 'thinking_type' and 'always_on' in caught.value.message


# --- backfill (R-2.0.7 admin task) -------------------------------------------------------

def _row(**data):
    return {'name': 'gpt-5.4', 'supports_reasoning': True, **data}


@pytest.mark.parametrize('data,outcome', [
    (_row(supports_reasoning=False), 'not_reasoning'),
    (_row(supports_reasoning=None), 'not_reasoning'),
    (_row(default_effort='high'), 'already_configured'),
    (_row(supported_efforts=[]), 'already_configured'),
    (_row(name='gpt-4-azure'), 'profile_without_reasoning'),
    (_row(name='gpt-5-chat-latest'), 'profile_without_reasoning'),
    (None, 'not_reasoning'),
])
def test_backfill_leaves_rows_it_must_not_touch(profiles, data, outcome):
    assert profiles.reasoning_backfill_patch(data) == (None, outcome)


@pytest.mark.parametrize('name,outcome,expected', [
    ('global.anthropic.claude-sonnet-5', 'anthropic-adaptive',
     {'thinking_type': 'adaptive', 'supported_efforts': ['low', 'medium', 'high', 'xhigh', 'max'], 'default_effort': 'medium'}),
    ('claude-fable-5-1', 'anthropic-fable-mythos',
     {'thinking_type': 'always_on', 'supported_efforts': ['low', 'medium', 'high', 'xhigh', 'max'], 'default_effort': 'medium'}),
    ('eu.anthropic.claude-haiku-4-5-20251001-v1:0', 'anthropic-legacy-budget',
     {'thinking_type': 'enabled', 'supported_efforts': ['low', 'medium', 'high'], 'default_effort': 'medium'}),
    ('global.openai.gpt-5.6-luna', 'openai-gpt-5-6',
     {'thinking_type': None, 'supported_efforts': ['none', 'low', 'medium', 'high', 'xhigh'], 'default_effort': 'medium'}),
    ('gpt-5-pro', 'openai-gpt-5-pro', {'thinking_type': None, 'supported_efforts': ['high'], 'default_effort': 'high'}),
    ('global.xai.grok-4.6', 'unrecognized',
     {'thinking_type': None, 'supported_efforts': ['low', 'medium', 'high'], 'default_effort': 'medium'}),
    ('model-router', 'unrecognized',
     {'thinking_type': None, 'supported_efforts': ['low', 'medium', 'high'], 'default_effort': 'medium'}),
])
def test_backfill_writes_the_profile_levels_with_medium_as_default(profiles, LlmModel, name, outcome, expected):
    patch, got = profiles.reasoning_backfill_patch(_row(name=name))
    assert (patch, got) == (expected, outcome)
    LlmModel.model_validate(_data(name=name, **patch))
    profiles.check_llm_model_profile_bounds('llm_model', _data(name=name, **patch))
    assert profiles.reasoning_backfill_patch({**_row(name=name), **patch}) == (None, 'already_configured')


def test_backfill_patch_does_not_alias_the_profile_list(profiles):
    patch, _ = profiles.reasoning_backfill_patch(_row(name='gpt-5.2'))
    patch['supported_efforts'].append('max')
    assert 'max' not in profiles.recognize_profile('gpt-5.2')['supported_efforts']


class _FakeSession:
    def __init__(self, rows):
        self.rows = rows
        self.executed = []
        self.commits = 0

    def query(self, model):
        return self

    def filter(self, *conditions):
        return self

    def all(self):
        return self.rows

    def execute(self, statement):
        self.executed.append(statement)

    def commit(self):
        self.commits += 1

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def task_module():
    package = PACKAGE + '_task'
    for suffix, path in [('', ROOT), ('.models', ROOT / 'models'), ('.methods', ROOT / 'methods')]:
        module = types.ModuleType(package + suffix)
        module.__path__ = [str(path)]
        sys.modules[module.__name__] = module
    sys.modules[f'{package}.common_utils'] = _stub('common_utils', get_public_project_id=lambda: 1)
    sys.modules[f'{package}.models.configuration'] = _stub('models.configuration', Configuration=_Configuration)
    yield importlib.import_module(f'{package}.methods.admin_tasks')
    for name in [m for m in sys.modules if m.startswith(package)]:
        del sys.modules[name]


def _task(task_module, session):
    method = task_module.Method.__new__(task_module.Method)
    method.context = types.SimpleNamespace(rpc_manager=types.SimpleNamespace(
        call=types.SimpleNamespace(project_list=lambda: [{'id': 1}])))
    task_module.db = types.SimpleNamespace(with_project_schema_session=lambda project_id: session)
    return method.backfill_llm_model_reasoning_profiles


def _stored(id_, **data):
    return _Configuration(id=id_, project_id=1, type='llm_model', section='llm', elitea_title=f'm{id_}',
                          author_id=1, shared=False, status_ok=True, data=_data(**data))


def test_task_dry_run_reports_without_writing(task_module):
    session = _FakeSession([
        _stored(1, name='global.anthropic.claude-sonnet-5'),
        _stored(2, name='gpt-4-azure'),
        _stored(3, name='gpt-4.1', supports_reasoning=False),
        _stored(4, name='gpt-5.4', default_effort='high', supported_efforts=['high']),
    ])
    result = _task(task_module, session)(param='project_id=all;dry_run')
    assert result == {'backfilled': 1, 'dry_run': True, 'failed_projects': [],
                      'skipped': {'profile_without_reasoning': 1, 'not_reasoning': 1, 'already_configured': 1}}
    assert session.executed == [] and session.commits == 0


def test_task_live_run_updates_only_backfillable_rows_and_keeps_updated_at(task_module):
    session = _FakeSession([_stored(1, name='global.openai.gpt-5.6-luna'), _stored(2, name='gpt-4-azure')])
    result = _task(task_module, session)(param='project_id=1')
    assert result['backfilled'] == 1 and result['skipped'] == {'profile_without_reasoning': 1}
    assert len(session.executed) == 1 and session.commits == 1
    compiled = session.executed[0].compile(dialect=postgresql.dialect())
    assert compiled.params['id_1'] == 1
    assert compiled.params['data'] == {**_data(name='global.openai.gpt-5.6-luna'), 'thinking_type': None,
                                       'supported_efforts': ['none', 'low', 'medium', 'high', 'xhigh'],
                                       'default_effort': 'medium'}
    assert 'updated_at=configuration.updated_at' in str(compiled).replace(' ', '')


def test_task_requires_a_project_selector(task_module):
    assert 'error' in _task(task_module, _FakeSession([]))(param='dry_run')


def test_task_counts_a_project_only_after_its_commit_succeeds(task_module):
    class _FailingSession(_FakeSession):
        def commit(self):
            raise RuntimeError('db down')

    session = _FailingSession([_stored(1, name='global.openai.gpt-5.6-luna'), _stored(2, name='gpt-4-azure')])
    result = _task(task_module, session)(param='project_id=1')
    assert result == {'backfilled': 0, 'skipped': {}, 'failed_projects': [1], 'dry_run': False}


def test_every_profile_offering_medium_defaults_to_it(profiles):
    for profile in profiles.PROFILES:
        if 'medium' in profile['supported_efforts']:
            assert profile['default_effort'] == 'medium', profile['id']


# --- re-run narrows configured rows to the profile (#6908) ---------------------------------

CODEX_STORED = ['none', 'low', 'medium', 'high']


@pytest.mark.parametrize('stored,default,outcome,expected', [
    (CODEX_STORED, 'medium', 'narrowed', {'supported_efforts': ['low', 'medium', 'high'], 'default_effort': 'medium'}),
    (['none', 'high'], 'high', 'narrowed', {'supported_efforts': ['high'], 'default_effort': 'high'}),
    (['none', 'minimal'], 'minimal', 'narrowed', {'supported_efforts': ['low', 'medium', 'high'], 'default_effort': 'medium'}),
    (['low', 'high'], 'high', 'already_configured', None),
])
def test_backfill_narrows_a_configured_codex_row_to_its_profile(profiles, LlmModel, stored, default, outcome, expected):
    row = _data(name='gpt-5.1-codex', supported_efforts=stored, default_effort=default)
    patch, got = profiles.reasoning_backfill_patch(row)
    assert (patch, got) == (expected, outcome)
    if patch:
        LlmModel.model_validate({**row, **patch})
        profiles.check_llm_model_profile_bounds('llm_model', {**row, **patch})
        assert profiles.reasoning_backfill_patch({**row, **patch}) == (None, 'already_configured')


def test_backfill_keeps_a_configured_default_whose_level_survives(profiles):
    row = _data(name='gpt-5.1-codex', supported_efforts=['none', 'low', 'high'], default_effort='high')
    assert profiles.reasoning_backfill_patch(row) == (
        {'supported_efforts': ['low', 'high'], 'default_effort': 'high'}, 'narrowed')


@pytest.mark.parametrize('row', [
    _data(name='global.openai.gpt-5.6-terra', supported_efforts=['none', 'low', 'medium', 'high', 'xhigh'],
          default_effort='medium'),
    _data(name='global.xai.grok-4.6', supported_efforts=['none', 'max'], default_effort='max'),
    _data(name='gpt-4-azure', supported_efforts=['low'], default_effort='low'),
    _data(name='claude-opus-4-6', thinking_type='adaptive'),
    _data(name='gpt-5.1-codex-mini', supported_efforts=['none', 'low', 'medium', 'high'], default_effort='medium'),
])
def test_backfill_leaves_configured_rows_it_has_no_proof_against(profiles, row):
    assert profiles.reasoning_backfill_patch(row) == (None, 'already_configured')


def test_task_rerun_narrows_a_configured_codex_row_and_keeps_updated_at(task_module):
    session = _FakeSession([
        _stored(1, name='gpt-5.1-codex', supported_efforts=CODEX_STORED, default_effort='medium'),
        _stored(2, name='global.openai.gpt-5.6-terra', supported_efforts=['none', 'low', 'medium', 'high', 'xhigh'],
                default_effort='medium'),
    ])
    result = _task(task_module, session)(param='project_id=1')
    assert result == {'backfilled': 1, 'dry_run': False, 'failed_projects': [], 'skipped': {'already_configured': 1}}
    compiled = session.executed[0].compile(dialect=postgresql.dialect())
    assert compiled.params['id_1'] == 1
    assert compiled.params['data'] == _data(name='gpt-5.1-codex', supported_efforts=['low', 'medium', 'high'],
                                            default_effort='medium')
    assert 'updated_at=configuration.updated_at' in str(compiled).replace(' ', '')
