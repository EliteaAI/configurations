"""Tier defaults move only on explicit acts: a tier flag turned on, or a rename of the current tier default (#7437)."""
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
PACKAGE = 'configurations_7437_test'
LOW_KEYS = ('default_llm_low_tier_model_name', 'default_llm_low_tier_model_project_id')
HIGH_KEYS = ('default_llm_high_tier_model_name', 'default_llm_high_tier_model_project_id')


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
    shared: Mapped[bool] = mapped_column(Boolean)
    status_ok: Mapped[bool] = mapped_column(Boolean)
    author_id: Mapped[int] = mapped_column(Integer)
    generation: Mapped[int] = mapped_column(Integer)


class _ConfigurationDetails:
    def __init__(self, config):
        self.config = config

    @classmethod
    def model_validate(cls, config):
        return cls(config)

    def model_dump(self, mode):
        return {'id': self.config.id, 'type': self.config.type, 'data': self.config.data}


class FakeVault:
    """Single-project vault double recording every read and write."""

    secrets = {}
    reads = 0
    writes = []

    @classmethod
    def from_project(cls, project_id):
        return cls()

    def get_secrets(self):
        FakeVault.reads += 1
        return dict(FakeVault.secrets)

    def set_secrets(self, secrets):
        FakeVault.writes.append(dict(secrets))
        FakeVault.secrets = dict(secrets)


def _stub(name, **attrs):
    module = types.ModuleType(f'{PACKAGE}.{name}')
    module.__dict__.update(attrs)
    sys.modules[module.__name__] = module


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
    _stub('common_utils', get_personal_project_id=None, get_public_project_id=None)
    _stub('local_tools', db=MagicMock(), store_secrets=MagicMock(), purge_secrets=None,
          event_manager=MagicMock(), log=MagicMock(), VaultClient=FakeVault)
    _stub('models.configuration', Configuration=_Configuration)
    _stub('models.pd.configuration', ConfigurationCreate=None, ConfigurationDetails=_ConfigurationDetails,
          ConfigurationCreateRpc=None, ConfigurationList=None)
    _stub('models.pd.registry', owned_entry=lambda *_: None, ADMIN_MANAGED_TYPES=frozenset({'oauth_app'}),
          CONFIG_TYPE_REGISTRY={})
    _stub('folder_access', folder_exclusion_clause=None)
    _stub('routing_access', validate_routing_write=lambda type_, project, payload, existing: payload)
    _stub('llm_model_profiles', check_llm_model_profile_bounds=lambda *_: None)
    _load('exceptions')
    yield _load('utils')
    for name in [m for m in sys.modules if m.startswith(PACKAGE)]:
        del sys.modules[name]


def update(utils, stored_data, payload, vault):
    config = _Configuration(id=4, project_id=1, type='llm_model', section='llm', elitea_title='m', author_id=1,
                            data=stored_data, shared=True, status_ok=True)
    session = MagicMock()
    session.query.return_value.filter_by.return_value.first.return_value = config
    sys.modules[f'{PACKAGE}.local_tools'].db.get_session.return_value.__enter__.return_value = session
    FakeVault.secrets = dict(vault)
    FakeVault.reads = 0
    FakeVault.writes = []
    utils.update_configuration(1, 4, payload)
    return FakeVault.writes


def pointing(keys, name, project_id=1):
    return {keys[0]: name, keys[1]: project_id}


LOW_MODEL = {'name': 'gpt-5-mini', 'low_tier': True}
OTHER_DEFAULT = pointing(LOW_KEYS, 'claude-haiku-5-5')


def test_status_update_does_not_touch_the_tier_default(utils):
    assert update(utils, LOW_MODEL, {'status_ok': True}, OTHER_DEFAULT) == []
    assert FakeVault.reads == 0


def test_unshare_does_not_touch_the_tier_default(utils):
    assert update(utils, LOW_MODEL, {'shared': False}, OTHER_DEFAULT) == []
    assert FakeVault.reads == 0


def test_turning_the_flag_on_makes_the_model_the_tier_default(utils):
    [written] = update(utils, {'name': 'gpt-5-mini', 'low_tier': False},
                       {'data': {'name': 'gpt-5-mini', 'low_tier': True}}, OTHER_DEFAULT)
    assert written == pointing(LOW_KEYS, 'gpt-5-mini')


def test_other_edits_with_the_flag_kept_on_do_not_move_the_tier_default(utils):
    payload = {'data': {**LOW_MODEL, 'description': 'edited'}}
    assert update(utils, LOW_MODEL, payload, OTHER_DEFAULT) == []


def test_renaming_the_current_tier_default_carries_the_pointer(utils):
    payload = {'data': {'name': 'gpt-5.1-mini', 'low_tier': True}}
    [written] = update(utils, LOW_MODEL, payload, pointing(LOW_KEYS, 'gpt-5-mini'))
    assert written == pointing(LOW_KEYS, 'gpt-5.1-mini')


def test_renaming_another_tier_model_leaves_the_pointer(utils):
    payload = {'data': {'name': 'gpt-5.1-mini', 'low_tier': True}}
    assert update(utils, LOW_MODEL, payload, OTHER_DEFAULT) == []


def test_moving_a_model_from_low_to_high_tier_points_only_high_at_it(utils):
    payload = {'data': {'name': 'gpt-5-mini', 'low_tier': False, 'high_tier': True}}
    [written] = update(utils, LOW_MODEL, payload, pointing(LOW_KEYS, 'gpt-5-mini'))
    assert written == {**pointing(LOW_KEYS, 'gpt-5-mini'), **pointing(HIGH_KEYS, 'gpt-5-mini')}
