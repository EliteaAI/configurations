"""The reasoning fields an LLM model stores (#6819, #7209): schema rules, read shape and the save path.

Capabilities are no longer read from the model name; the stored fields must still be consistent with each other,
and a Claude thinking mode never stores a level ChatAnthropic rejects.
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
PACKAGE = 'configurations_reasoning_fields'
CREDS = {'elitea_title': 'creds', 'private': False}


def _load_llm_model():
    spec = importlib.util.spec_from_file_location('llm_model_reasoning_fields', ROOT / 'models/pd/llm_model.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
    generation: Mapped[int] = mapped_column(Integer, nullable=True)


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
        'models.pd.registry': _stub('models.pd.registry', owned_entry=lambda *_: None, ADMIN_MANAGED_TYPES=frozenset({'oauth_app'}), CONFIG_TYPE_REGISTRY={
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


@pytest.mark.parametrize('thinking_type,levels,rejected', [
    ('adaptive', ['none', 'low'], "'adaptive' cannot offer 'none'"),
    ('adaptive', ['minimal', 'low'], "'adaptive' cannot offer 'minimal'"),
    ('always_on', ['minimal', 'low'], "'always_on' cannot offer 'minimal'"),
    ('always_on', ['none', 'minimal', 'low'], "'always_on' cannot offer 'none', 'minimal'"),
])
def test_claude_thinking_modes_never_store_levels_chat_anthropic_rejects(LlmModel, thinking_type, levels, rejected):
    with pytest.raises(ValidationError) as caught:
        LlmModel.model_validate(_data(thinking_type=thinking_type, supported_efforts=levels, default_effort='low'))
    assert rejected in _errors(caught)['supported_efforts']


@pytest.mark.parametrize('thinking_type', ['adaptive', 'always_on'])
def test_claude_thinking_modes_store_every_level_from_low_to_max(LlmModel, thinking_type):
    levels = ['low', 'medium', 'high', 'xhigh', 'max']
    model = LlmModel.model_validate(_data(thinking_type=thinking_type, supported_efforts=levels, default_effort='medium'))
    assert model.supported_efforts == levels


def test_models_without_a_thinking_mode_still_store_none_and_minimal(LlmModel):
    model = LlmModel.model_validate(_data(supported_efforts=['none', 'minimal', 'low'], default_effort='low'))
    assert model.supported_efforts == ['none', 'minimal', 'low']


def _store(utils_module, **data):
    session = utils_module.db.get_session.return_value.__enter__.return_value
    session.query.return_value.filter_by.return_value.first.return_value = _Configuration(
        id=7, project_id=1, type='llm_model', elitea_title='model', author_id=3, data=_data(**data),
        shared=False, status_ok=True,
    )


def test_update_rejects_none_on_a_claude_thinking_mode_whatever_the_model_is_called(utils_module):
    _store(utils_module)
    with pytest.raises(utils_module.ConfigurationError) as caught:
        utils_module.update_configuration(1, 7, {'data': _data(
            name='my-private-alias', thinking_type='adaptive', supported_efforts=['none', 'low'], default_effort='low',
        )})
    assert caught.value.field == 'supported_efforts' and "cannot offer 'none'" in caught.value.message


@pytest.mark.parametrize('data', [
    {'name': 'gpt-5.5-pro', 'supported_efforts': ['medium', 'high', 'xhigh'], 'default_effort': 'medium'},
    {'name': 'claude-sonnet-5-5', 'thinking_type': 'always_on',
     'supported_efforts': ['low', 'medium', 'high', 'xhigh', 'max'], 'default_effort': 'medium'},
    {'name': 'gpt-4.1'},
])
def test_update_is_not_bounded_by_the_model_name(utils_module, data):
    _store(utils_module)
    try:
        utils_module.update_configuration(1, 7, {'data': _data(**data)})
    except utils_module.ConfigurationError as error:
        pytest.fail(f'the save was rejected: {error.field}: {error.message}')
