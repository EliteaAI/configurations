import importlib.util
import pathlib
import sys
import types
from unittest.mock import MagicMock

import pytest
from sqlalchemy import Boolean, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

ROOT = pathlib.Path(__file__).resolve().parents[2]
PACKAGE = 'configurations_6891_test'


class _Base(DeclarativeBase):
    pass


class _Configuration(_Base):
    __tablename__ = 'configuration'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(Integer)
    elitea_title: Mapped[str] = mapped_column(String)
    type: Mapped[str] = mapped_column(String)
    section: Mapped[str] = mapped_column(String)
    data: Mapped[dict] = mapped_column(JSONB)
    status_ok: Mapped[bool] = mapped_column(Boolean)
    author_id: Mapped[int] = mapped_column(Integer)


class _ConfigurationDetails:
    def __init__(self, config):
        self.config = config

    @classmethod
    def model_validate(cls, config):
        return cls(config)

    def model_dump(self, mode):
        return {'id': self.config.id, 'type': self.config.type, 'section': self.config.section,
                'data': self.config.data}


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
def utils():
    for suffix, path in [('', ROOT), ('.models', ROOT / 'models'), ('.models.pd', ROOT / 'models/pd')]:
        package = types.ModuleType(PACKAGE + suffix)
        package.__path__ = [str(path)]
        sys.modules[package.__name__] = package
    stubs = {
        'common_utils': _stub('common_utils', get_personal_project_id=None, get_public_project_id=None),
        'local_tools': _stub('local_tools', db=MagicMock(), store_secrets=MagicMock(), purge_secrets=None,
                             event_manager=MagicMock(), log=MagicMock(), VaultClient=None),
        'models.configuration': _stub('models.configuration', Configuration=_Configuration),
        'models.pd.configuration': _stub('models.pd.configuration', ConfigurationCreate=None,
                                         ConfigurationDetails=_ConfigurationDetails, ConfigurationCreateRpc=None,
                                         ConfigurationList=None),
        'models.pd.registry': _stub('models.pd.registry', CONFIG_TYPE_REGISTRY={}),
        'folder_access': _stub('folder_access', folder_exclusion_clause=None),
        'routing_access': _stub('routing_access', validate_routing_write=lambda type_, project, payload, existing: payload),
        'llm_model_profiles': _stub('llm_model_profiles', check_llm_model_profile_bounds=lambda *_: None),
    }
    for module in stubs.values():
        sys.modules[module.__name__] = module
    _load('exceptions')
    yield _load('utils')
    for name in [m for m in sys.modules if m.startswith(PACKAGE)]:
        del sys.modules[name]


def _stored(type_, section, data):
    config = _Configuration(id=4, project_id=2, type=type_, section=section, elitea_title='c', author_id=1,
                            data=data, status_ok=True)
    session = MagicMock()
    session.query.return_value.filter_by.return_value.first.return_value = config
    sys.modules[f'{PACKAGE}.local_tools'].db.get_session.return_value.__enter__.return_value = session
    events = sys.modules[f'{PACKAGE}.local_tools'].event_manager
    events.reset_mock()
    return events


def _fired(events, name):
    return [call.args[1] for call in events.fire_event.call_args_list if call.args[0] == name]


def test_credential_edit_fires_updated_with_the_data_before_the_edit(utils):
    events = _stored('open_ai', 'ai_credentials', {'api_key': '{{secret.old}}', 'api_base': 'https://a'})
    utils.update_configuration(2, 4, {'data': {'api_key': '{{secret.new}}', 'api_base': 'https://a'}})
    [payload] = _fired(events, 'configuration_updated')
    assert payload['data'] == {'api_key': '{{secret.new}}', 'api_base': 'https://a'}
    assert payload['previous_data'] == {'api_key': '{{secret.old}}', 'api_base': 'https://a'}


def test_model_rename_carries_the_old_name(utils):
    creds = {'elitea_title': 'creds', 'private': False}
    events = _stored('llm_model', 'llm', {'name': 'gpt-4o', 'ai_credentials': creds})
    utils.update_configuration(2, 4, {'data': {'name': 'gpt-5', 'ai_credentials': creds}})
    [payload] = _fired(events, 'configuration_updated')
    assert (payload['previous_data']['name'], payload['data']['name']) == ('gpt-4o', 'gpt-5')


def test_status_reply_without_data_fires_only_status_changed(utils):
    events = _stored('open_ai', 'ai_credentials', {'api_key': '{{secret.old}}'})
    utils.update_configuration(2, 4, {'status_ok': True})
    assert _fired(events, 'configuration_updated') == []
    assert len(_fired(events, 'configuration_status_changed')) == 1


def test_routing_status_update_still_fires_updated(utils):
    events = _stored('auto_routing', 'auto_routing', {'enabled': True})
    utils.update_configuration(2, 4, {'status_ok': True})
    assert len(_fired(events, 'configuration_updated')) == 1
