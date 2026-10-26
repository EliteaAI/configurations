import importlib.util
import pathlib
import sys
import types
import uuid as uuidlib
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
PACKAGE = 'configurations_6943_test'

CURRENT, PERSONAL, PUBLIC, OTHER = 10, 20, 1, 30

STATE = {'rows': {}}  # project_id -> list of rows


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


class _Query:
    def __init__(self, rows):
        self.rows = rows

    def filter_by(self, **kw):
        return _Query([r for r in self.rows if all(getattr(r, k) == v for k, v in kw.items())])

    def first(self):
        return self.rows[0] if self.rows else None


class _Session:
    def __init__(self, project_id):
        self.rows = STATE['rows'].get(project_id, [])
        self.commit = MagicMock()
        self.refresh = MagicMock()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def query(self, _model):
        return _Query(self.rows)


class _Vault:
    def __init__(self, project_id):
        self.project_id = project_id

    def unsecret(self, data):
        return {k: f'plain-{v}' if str(v).startswith('{{secret.') else v for k, v in data.items()}


@pytest.fixture(scope='module')
def utils():
    for suffix, path in [('', ROOT), ('.models', ROOT / 'models'), ('.models.pd', ROOT / 'models/pd')]:
        package = types.ModuleType(PACKAGE + suffix)
        package.__path__ = [str(path)]
        sys.modules[package.__name__] = package
    db = MagicMock()
    db.get_session.side_effect = lambda project_id: _Session(project_id)
    stubs = {
        'common_utils': _stub('common_utils', get_personal_project_id=lambda user_id: PERSONAL,
                              get_public_project_id=lambda: PUBLIC),
        'local_tools': _stub('local_tools', db=db, store_secrets=MagicMock(), purge_secrets=None,
                             event_manager=MagicMock(), log=MagicMock(), VaultClient=_Vault),
        'models.configuration': _stub('models.configuration', Configuration=object),
        'models.pd.configuration': _stub('models.pd.configuration', ConfigurationCreate=None,
                                         ConfigurationDetails=SimpleNamespace(model_validate=lambda c: SimpleNamespace(
                                             model_dump=lambda mode: {'id': c.id, 'data': c.data})),
                                         ConfigurationCreateRpc=None, ConfigurationList=None),
        'models.pd.registry': _stub('models.pd.registry', owned_entry=lambda *_: None, ADMIN_MANAGED_TYPES=frozenset({'oauth_app'}), CONFIG_TYPE_REGISTRY={}),
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


def _row(project_id, title='cred', shared=False, type_='open_ai', data=None, generation=1):
    return SimpleNamespace(id=len(STATE['rows'].get(project_id, [])) + 1, uuid=uuidlib.uuid4(), project_id=project_id,
                           elitea_title=title, shared=shared, type=type_, section='ai_credentials',
                           data=data if data is not None else {'api_key': '{{secret.k}}', 'url': 'u'},
                           generation=generation, status_ok=True, status_logs=None)


@pytest.fixture(autouse=True)
def _reset():
    STATE['rows'] = {}
    yield


def _put(row):
    STATE['rows'].setdefault(row.project_id, []).append(row)
    return row


def test_uuid_resolves_in_current_project(utils):
    row = _put(_row(CURRENT))
    payload = {'configuration_uuid': str(row.uuid)}
    utils.expand_configuration(payload, CURRENT, user_id=1)
    assert payload['url'] == 'u'
    assert payload['configuration_uuid'] == str(row.uuid)
    assert payload['configuration_project_id'] == CURRENT
    assert payload['configuration_type'] == 'open_ai'


def test_uuid_with_project_id_resolves_there(utils):
    row = _put(_row(PUBLIC, shared=True))
    payload = {'configuration_uuid': str(row.uuid), 'configuration_project_id': PUBLIC}
    utils.expand_configuration(payload, CURRENT, user_id=1, unsecret=True)
    assert payload['configuration_project_id'] == PUBLIC
    assert payload['api_key'] == 'plain-{{secret.k}}'


def test_uuid_in_other_project_not_shared_is_refused(utils):
    row = _put(_row(OTHER, shared=False))
    payload = {'configuration_uuid': str(row.uuid), 'configuration_project_id': OTHER}
    with pytest.raises(LookupError) as err:
        utils.expand_configuration(payload, CURRENT, user_id=1)
    assert err.value.args[1]['configuration_uuid'] == str(row.uuid)


def test_uuid_in_public_project_not_shared_is_refused(utils):
    row = _put(_row(PUBLIC, shared=False))
    payload = {'configuration_uuid': str(row.uuid), 'configuration_project_id': PUBLIC}
    with pytest.raises(LookupError):
        utils.expand_configuration(payload, CURRENT, user_id=1)


def test_uuid_of_an_admin_managed_oauth_app_is_refused(utils):
    """#7149: an OAuth app is reachable only through a credential's oauth_app_id."""
    row = _put(_row(PUBLIC, shared=True))
    row.type = 'oauth_app'
    payload = {'configuration_uuid': str(row.uuid), 'configuration_project_id': PUBLIC}
    with pytest.raises(LookupError):
        utils.expand_configuration(payload, CURRENT, user_id=1)


def test_uuid_in_other_shared_non_public_project_is_refused(utils):
    row = _put(_row(OTHER, shared=True))
    payload = {'configuration_uuid': str(row.uuid), 'configuration_project_id': OTHER}
    with pytest.raises(LookupError):
        utils.expand_configuration(payload, CURRENT, user_id=1)


def test_private_uuid_resolves_in_personal_project(utils):
    row = _put(_row(PERSONAL))
    payload = {'configuration_uuid': str(row.uuid), 'private': True}
    utils.expand_configuration(payload, CURRENT, user_id=1)
    assert payload['configuration_project_id'] == PERSONAL


def test_private_uuid_requires_user_id(utils):
    row = _put(_row(PERSONAL))
    with pytest.raises(ValueError):
        utils.expand_configuration({'configuration_uuid': str(row.uuid), 'private': True}, CURRENT)


def test_uuid_never_falls_back_to_title_or_public_project(utils):
    _put(_row(PUBLIC, title='cred', shared=True))  # same-title shared record exists
    missing = str(uuidlib.uuid4())
    payload = {'configuration_uuid': missing, 'elitea_title': 'cred'}
    with pytest.raises(LookupError) as err:
        utils.expand_configuration(payload, CURRENT, user_id=1)
    assert err.value.args[1] == {'personal_project_lookup': False, 'configuration_uuid': missing}


def test_uuid_wins_over_title(utils):
    by_title = _put(_row(CURRENT, title='cred', data={'url': 'by-title'}))
    by_uuid = _put(_row(CURRENT, title='other', data={'url': 'by-uuid'}))
    payload = {'configuration_uuid': str(by_uuid.uuid), 'elitea_title': 'cred'}
    utils.expand_configuration(payload, CURRENT, user_id=1)
    assert payload['url'] == 'by-uuid'
    assert by_title.uuid != by_uuid.uuid


def test_malformed_uuid_is_value_error(utils):
    with pytest.raises(ValueError, match='configuration_uuid'):
        utils.expand_configuration({'configuration_uuid': 'not-a-uuid'}, CURRENT, user_id=1)


def test_nested_uuid_is_expanded(utils):
    row = _put(_row(CURRENT))
    payload = {'settings': {'configuration_uuid': str(row.uuid)}}
    utils.expand_configuration(payload, CURRENT, user_id=1)
    assert payload['settings']['url'] == 'u'


def test_same_uuid_twice_is_a_recursion_error(utils):
    row = _put(_row(CURRENT))
    payload = {'a': {'configuration_uuid': str(row.uuid)}, 'b': {'configuration_uuid': str(row.uuid)}}
    with pytest.raises(ValueError, match='Recursion'):
        utils.expand_configuration(payload, CURRENT, user_id=1)


def test_title_path_unchanged_including_public_shared_fallback(utils):
    row = _put(_row(PUBLIC, title='cred', shared=True))
    payload = {'elitea_title': 'cred', 'private': False}
    utils.expand_configuration(payload, CURRENT, user_id=1)
    assert payload['configuration_uuid'] == str(row.uuid)
    assert payload['configuration_project_id'] == PUBLIC
    assert payload['url'] == 'u'


def test_title_path_not_found_keeps_legacy_error(utils):
    with pytest.raises(LookupError) as err:
        utils.expand_configuration({'elitea_title': 'nope'}, CURRENT, user_id=1)
    assert err.value.args[1] == {'personal_project_lookup': False, 'elitea_title': 'nope'}


def test_generation_is_in_both_paths(utils):
    row = _put(_row(CURRENT, title='cred', generation=7))
    by_uuid = {'configuration_uuid': str(row.uuid)}
    by_title = {'elitea_title': 'cred'}
    utils.expand_configuration(by_uuid, CURRENT, user_id=1)
    utils.expand_configuration(by_title, CURRENT, user_id=1)
    assert by_uuid['configuration_generation'] == by_title['configuration_generation'] == 7


def test_update_data_bumps_generation(utils):
    row = _put(_row(CURRENT, generation=3))
    utils.update_configuration(CURRENT, row.id, {'data': {'url': 'new'}})
    assert row.generation == 4


def test_update_without_data_keeps_generation(utils):
    row = _put(_row(CURRENT, generation=3))
    utils.update_configuration(CURRENT, row.id, {'status_ok': False})
    assert row.generation == 3


def test_update_expected_generation_match_succeeds(utils):
    row = _put(_row(CURRENT, generation=3))
    utils.update_configuration(CURRENT, row.id, {'data': {'url': 'new'}}, expected_generation=3)
    assert (row.generation, row.data) == (4, {'url': 'new'})


def test_update_expected_generation_mismatch_changes_nothing(utils):
    row = _put(_row(CURRENT, generation=3))
    with pytest.raises(utils.ConfigurationError) as err:
        utils.update_configuration(CURRENT, row.id, {'data': {'url': 'new'}}, expected_generation=2)
    assert err.value.field == 'generation'
    assert (row.generation, row.data['url']) == (3, 'u')


# --- add_configuration_generation_column admin task ------------------------------------------------

class _AdminSession:
    def __init__(self, columns):
        self.columns = columns
        self.executed = []
        self.commits = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, statement, params=None):
        self.executed.append((str(statement), params))
        if 'information_schema' in str(statement):
            return [(c,) for c in self.columns[params['schema']]]

    def commit(self):
        self.commits += 1


@pytest.fixture
def admin_task(monkeypatch):
    package = PACKAGE + '_task'
    for suffix, path in [('', ROOT), ('.models', ROOT / 'models'), ('.methods', ROOT / 'methods')]:
        module = types.ModuleType(package + suffix)
        module.__path__ = [str(path)]
        sys.modules[module.__name__] = module
    sys.modules[f'{package}.common_utils'] = _stub('common_utils', get_public_project_id=lambda: 1)
    sys.modules[f'{package}.models.configuration'] = _stub('models.configuration', Configuration=object)
    sys.modules[f'{package}.llm_model_profiles'] = _stub('llm_model_profiles', reasoning_backfill_patch=None)
    monkeypatch.setattr(sys.modules['tools'], 'project_constants', {'PROJECT_SCHEMA_TEMPLATE': 'p_{}'}, raising=False)
    module = importlib.import_module(f'{package}.methods.admin_tasks')
    yield module
    for name in [m for m in sys.modules if m.startswith(package)]:
        del sys.modules[name]


def _run(admin_task, columns, param):
    sessions = {}

    def open_session(project_id):
        sessions[project_id] = _AdminSession(columns)
        return sessions[project_id]

    method = admin_task.Method.__new__(admin_task.Method)
    method.context = types.SimpleNamespace(rpc_manager=types.SimpleNamespace(
        call=types.SimpleNamespace(project_list=lambda: [{'id': 1}, {'id': 2}, {'id': 3}])))
    admin_task.db = types.SimpleNamespace(with_project_schema_session=open_session)
    return method.add_configuration_generation_column(param=param), sessions


COLUMNS = {'p_1': ['id', 'generation'], 'p_2': ['id'], 'p_3': []}


def _alters(sessions):
    return {pid: [sql for sql, _ in s.executed if sql.startswith('ALTER')] for pid, s in sessions.items()}


def test_task_dry_run_reports_missing_schemas_and_writes_nothing(admin_task):
    result, sessions = _run(admin_task, COLUMNS, 'project_id=all;dry_run')
    assert result == {'added': 0, 'missing': [2], 'no_table': [3], 'failed_projects': [], 'dry_run': True}
    assert not any(_alters(sessions).values()) and not any(s.commits for s in sessions.values())


def test_task_live_run_alters_only_schemas_missing_the_column(admin_task):
    result, sessions = _run(admin_task, COLUMNS, 'project_id=all')
    assert result == {'added': 1, 'missing': [2], 'no_table': [3], 'failed_projects': [], 'dry_run': False}
    assert _alters(sessions) == {1: [], 2: ['ALTER TABLE p_2.configuration ADD COLUMN IF NOT EXISTS generation '
                                            'INTEGER NOT NULL DEFAULT 1'], 3: []}
    assert sessions[2].commits == 1 and sessions[1].commits == 0


def test_task_schema_name_is_a_bound_parameter_in_the_lookup(admin_task):
    _, sessions = _run(admin_task, COLUMNS, 'project_id=2;dry_run')
    assert sessions[2].executed[0][1] == {'schema': 'p_2'}


def test_task_single_project_selector(admin_task):
    result, sessions = _run(admin_task, COLUMNS, 'project_id=2')
    assert list(sessions) == [2] and result['added'] == 1


@pytest.mark.parametrize('param', ['dry_run', '', 'project_id=abc'])
def test_task_rejects_a_missing_or_bad_selector(admin_task, param):
    result, sessions = _run(admin_task, COLUMNS, param)
    assert 'error' in result and not sessions
