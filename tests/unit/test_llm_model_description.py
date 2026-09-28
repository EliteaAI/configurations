"""#6766: LLM models carry an optional display-only description of at most 40 characters.

Create stores the validated dump, update stores the raw payload, and the model listing reads the
column straight from the JSON data, so each of those paths is exercised here.
"""
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
PACKAGE = 'configurations_description_test'
CREDS = {'elitea_title': 'creds', 'private': False}
FORTY = 'x' * 40


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


def _stub(name, **attrs):
    module = types.ModuleType(f'{PACKAGE}.{name}')
    module.__dict__.update(attrs)
    return module


def _load(name):
    spec = importlib.util.spec_from_file_location(f'{PACKAGE}.{name}', ROOT / (name.replace('.', '/') + '.py'))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope='module')
def modules():
    for suffix, path in [('', ROOT), ('.models', ROOT / 'models'), ('.models.pd', ROOT / 'models/pd')]:
        package = types.ModuleType(PACKAGE + suffix)
        package.__path__ = [str(path)]
        sys.modules[package.__name__] = package
    llm_model = _load('models.pd.llm_model')
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
            'embedding_model': types.SimpleNamespace(
                model=llm_model.EmbeddingModel, config_schema=llm_model.EmbeddingModel.model_json_schema()
            ),
        }),
        'folder_access': _stub('folder_access', folder_exclusion_clause=None),
        'routing_access': _stub('routing_access', validate_routing_write=lambda type_, project, payload, existing: payload),
    }
    for name, module in stubs.items():
        sys.modules[module.__name__] = module
    _load('exceptions')
    yield types.SimpleNamespace(llm_model=llm_model, utils=_load('utils'))
    for name in [m for m in sys.modules if m.startswith(PACKAGE)]:
        del sys.modules[name]


def _data(**overrides):
    return {'name': 'gpt-5.4', 'ai_credentials': CREDS, **overrides}


def test_forty_characters_accepted(modules):
    assert modules.llm_model.LlmModel.model_validate(_data(description=FORTY)).description == FORTY


def test_forty_one_characters_rejected_on_description_field(modules):
    with pytest.raises(ValidationError) as caught:
        modules.llm_model.LlmModel.model_validate(_data(description=FORTY + 'x'))
    assert caught.value.errors()[0]['loc'] == ('description',)


def test_surrounding_spaces_are_trimmed_before_the_length_check(modules):
    assert modules.llm_model.LlmModel.model_validate(_data(description=f'  {FORTY}  ')).description == FORTY


@pytest.mark.parametrize('blank', ['', '   ', None])
def test_blank_description_becomes_none(modules, blank):
    assert modules.llm_model.LlmModel.model_validate(_data(description=blank)).description is None


def test_description_is_optional_and_kept_by_the_create_dump(modules):
    LlmModel = modules.llm_model.LlmModel
    assert LlmModel.model_validate(_data()).model_dump()['description'] is None
    assert LlmModel.model_validate(_data(description='Fast for everyday tasks')).model_dump(
        mode='python')['description'] == 'Fast for everyday tasks'


def test_models_listing_selects_description_as_text(modules):
    query = modules.utils.get_configuration_llm_models_with_limits_query(Session(), 1, [])
    assert 'description' in [column['name'] for column in query.column_descriptions]
    compiled = query.statement.compile(dialect=postgresql.dialect())
    description_sql = re.search(r"configuration\.data ->> %\((\w+)\)s AS description", str(compiled))
    assert description_sql and compiled.params[description_sql.group(1)] == 'description'


def test_listed_description_reaches_the_response_item(modules):
    row = types.SimpleNamespace(name='gpt-5.4', display_name='GPT', project_id=1, shared=True,
                                description='Best for coding and agents')
    item = modules.llm_model.LlmModelList.model_validate(row).model_dump(mode='json')
    assert item['description'] == 'Best for coding and agents'
    row_without = types.SimpleNamespace(name='gpt-5.4', display_name='GPT', project_id=1, description=None)
    assert modules.llm_model.LlmModelList.model_validate(row_without).model_dump(mode='json')['description'] is None


def _stored_config(**data):
    return _Configuration(id=7, project_id=1, type='llm_model', elitea_title='gpt', author_id=3,
                          data=_data(**data), shared=False, status_ok=True)


def _session_for(config):
    session = MagicMock()
    session.query.return_value.filter_by.return_value.first.return_value = config
    modules_db = sys.modules[f'{PACKAGE}.local_tools'].db
    modules_db.get_session.return_value.__enter__.return_value = session
    return session


def test_update_stores_trimmed_description(modules):
    config = _stored_config()
    _session_for(config)
    modules.utils.update_configuration(1, 7, {'data': _data(description='  Fast for everyday tasks ')})
    assert config.data['description'] == 'Fast for everyday tasks'


def test_update_stores_whitespace_only_description_as_none(modules):
    config = _stored_config(description='Old')
    _session_for(config)
    modules.utils.update_configuration(1, 7, {'data': _data(description='   ')})
    assert config.data['description'] is None


def test_update_without_description_leaves_payload_untouched(modules):
    config = _stored_config()
    _session_for(config)
    modules.utils.update_configuration(1, 7, {'data': _data()})
    assert 'description' not in config.data


def test_update_rejects_forty_one_characters_and_saves_nothing(modules):
    config = _stored_config(description='Old')
    session = _session_for(config)
    with pytest.raises(sys.modules[f'{PACKAGE}.exceptions'].ConfigurationError) as caught:
        modules.utils.update_configuration(1, 7, {'data': _data(description=FORTY + 'x')})
    assert caught.value.field == 'description'
    assert config.data['description'] == 'Old'
    session.commit.assert_not_called()


def test_update_of_a_non_llm_configuration_with_a_description_key_is_rejected_as_unknown_property(modules):
    config = _stored_config()
    config.type = 'embedding_model'
    session = _session_for(config)
    with pytest.raises(sys.modules[f'{PACKAGE}.exceptions'].ConfigurationError) as caught:
        modules.utils.update_configuration(1, 7, {'data': {'name': 'emb', 'ai_credentials': CREDS, 'description': 'x'}})
    assert caught.value.field == 'description'
    assert 'not valid' in caught.value.message
    session.commit.assert_not_called()
