"""Exercise the real routing reader and its query/precedence with a flat ORM fixture."""
import importlib.util
import json
import pathlib
import sys
import types

import pytest


ROOT = pathlib.Path(__file__).resolve().parents[2]
PACKAGE = 'configurations_routing_inventory_test'


class Column:
    def __init__(self, name, read=None):
        self.name = name
        self.read = read or (lambda row: row.get(name))

    def __getitem__(self, key):
        return Column(key, lambda row: (self.read(row) or {}).get(key))

    @property
    def astext(self):
        return self

    def label(self, name):
        return Column(name, self.read)

    def __eq__(self, value):
        return lambda row: self.read(row) == value

    def is_(self, value):
        return lambda row: self.read(row) is value


class Session:
    def __init__(self, database):
        self.database = database

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def query(self, *fields):
        self.fields = fields
        self.database.columns.append([field.name for field in fields])
        return self

    def filter(self, *filters):
        self.filters = filters
        return self

    def limit(self, limit):
        self.row_limit = limit
        return self

    def all(self):
        rows = [row for row in self.database.rows if all(f(row) for f in self.filters)]
        return [types.SimpleNamespace(_mapping={field.name: field.read(row) for field in self.fields})
                for row in rows[:self.row_limit]]


@pytest.fixture
def inventory(monkeypatch):
    database = types.SimpleNamespace(rows=[], columns=[], sessions=[])
    def get_session(project):
        database.sessions.append(project)
        return Session(database)
    database.get_session = get_session
    actor_calls = []
    hidden = set()
    def folder_filter(project, column, user_id=None):
        actor_calls.append((project, user_id))
        return lambda row: (project, user_id, row['id']) not in hidden
    configuration = type('Configuration', (), {key: Column(key) for key in (
        'id', 'uuid', 'project_id', 'shared', 'status_ok', 'label', 'updated_at',
        'created_at', 'data', 'section',
    )})
    for suffix, values in {
        '': {'__path__': [str(ROOT)]},
        '.rpc': {'__path__': [str(ROOT / 'rpc')]},
        '.common_utils': {'get_public_project_id': lambda: 1},
        '.folder_access': {'folder_exclusion_clause': folder_filter},
        '.local_tools': {'db': database},
        '.models': {'__path__': []},
        '.models.configuration': {'Configuration': configuration},
    }.items():
        module = types.ModuleType(PACKAGE + suffix)
        module.__dict__.update(values)
        monkeypatch.setitem(sys.modules, module.__name__, module)
    loaded = {}
    for name in ('routing_models', 'rpc.routing_models'):
        spec = importlib.util.spec_from_file_location(PACKAGE + '.' + name, ROOT / (name.replace('.', '/') + '.py'))
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, spec.name, module)
        spec.loader.exec_module(module)
        loaded[name] = module
    return types.SimpleNamespace(module=loaded['routing_models'],
        rpc=loaded['rpc.routing_models'].RPC(), db=database, actor_calls=actor_calls, hidden=hidden)


def row(ident, project, name='model-x', *, shared=False, healthy=True, **capabilities):
    return {'id': ident, 'uuid': f'configuration-{ident}', 'project_id': project,
        'section': 'llm', 'shared': shared, 'status_ok': healthy, 'label': f'Model {ident}',
        'updated_at': '2026-09-16T00:00:00', 'created_at': '2026-09-15T00:00:00',
        'data': {'name': name, 'context_window': 128000, 'max_output_tokens': 8000,
                 'supports_reasoning': True, 'supports_vision': False,
                 'openai_compatible': False, 'low_tier': False, 'high_tier': True,
                 'ai_credentials': {'secret': 'DO-NOT-READ'}, **capabilities}}


@pytest.mark.parametrize('reverse', [False, True])
def test_project_overrides_shared_without_merging_limits(inventory, reverse):
    rows = [row(1, 1, shared=True, context_window=1000000, max_output_tokens=64000),
            row(2, 7, context_window=32000, max_output_tokens=4000)]
    inventory.db.rows = rows[::-1] if reverse else rows
    snapshot = inventory.rpc.configurations_get_routing_models(7, 42)
    assert snapshot['public_project_id'] == 1
    assert len(snapshot['items']) == 1
    chosen = snapshot['items'][0]
    assert (chosen['project_id'], chosen['configuration_id'], chosen['configuration_uuid']) == (7, 2, 'configuration-2')
    assert (chosen['context_window'], chosen['max_output_tokens']) == (32000, 4000)
    assert chosen['available'] is True and chosen['exclusion_reason'] is None
    assert inventory.actor_calls == [(7, 42), (1, 42)]
    assert inventory.db.sessions == [7, 1]


@pytest.mark.parametrize('reverse', [False, True])
def test_project_a_b_plus_shared_b_c_retains_a_b_c(inventory, reverse):
    rows = [row(1, 7, name='A'), row(2, 7, name='B'),
            row(3, 1, name='B', shared=True), row(4, 1, name='C', shared=True)]
    inventory.db.rows = rows[::-1] if reverse else rows
    items = inventory.rpc.configurations_get_routing_models(7, 42)['items']
    assert {item['name']: (item['project_id'], item['configuration_id']) for item in items} == {
        'A': (7, 1), 'B': (7, 2), 'C': (1, 4)}


@pytest.mark.parametrize('healthy,limits,reason', [
    (False, {}, 'unhealthy_configuration'),
    (True, {'context_window': 0}, 'invalid_model_limits'),
    (True, {'max_output_tokens': '8000'}, 'invalid_model_limits'),
])
def test_unusable_private_definition_still_shadows_public(inventory, healthy, limits, reason):
    inventory.db.rows = [row(1, 1, shared=True), row(2, 7, healthy=healthy, **limits)]
    items = inventory.module.get_routing_models(7, 42)['items']
    assert len(items) == 1
    assert items[0]['project_id'] == 7 and items[0]['available'] is False
    assert items[0]['exclusion_reason'] == reason


def test_folder_invisible_private_definition_neither_leaks_nor_shadows(inventory):
    inventory.db.rows = [row(1, 1, shared=True), row(2, 7), row(3, 7, name='hidden-only')]
    inventory.hidden.update({(7, 42, 2), (7, 42, 3)})
    snapshot = inventory.module.get_routing_models(7, 42)
    assert [(item['project_id'], item['configuration_id']) for item in snapshot['items']] == [(1, 1)]
    assert 'hidden-only' not in json.dumps(snapshot)
    other_actor = inventory.module.get_routing_models(7, 43)
    assert {item['configuration_id'] for item in other_actor['items']} == {2, 3}
    assert snapshot['revision'] != other_actor['revision']


def test_unrelated_unshared_and_non_llm_rows_are_not_inventory(inventory):
    inventory.db.rows = [row(1, 1, shared=False), row(2, 8),
        {**row(3, 7), 'section': 'ai_credentials'}, row(4, 1, name='visible', shared=True)]
    assert [item['name'] for item in inventory.module.get_routing_models(7, 42)['items']] == ['visible']


def test_public_caller_reads_once_and_sees_own_nonshared_models(inventory):
    inventory.db.rows = [row(1, 1, shared=False)]
    assert inventory.module.get_routing_models(1, 42)['items'][0]['available'] is True
    assert inventory.db.sessions == [1]


@pytest.mark.parametrize('reverse', [False, True])
def test_ambiguous_same_owner_fails_closed_and_shadows_shared(inventory, reverse):
    rows = [row(1, 1, shared=True), row(2, 7, context_window=32000), row(3, 7, max_output_tokens=16000)]
    inventory.db.rows = rows[::-1] if reverse else rows
    items = inventory.module.get_routing_models(7, 42)['items']
    assert len(items) == 1
    item = items[0]
    assert item['project_id'] == 7 and item['available'] is False
    assert item['exclusion_reason'] == 'ambiguous_model_configuration'
    assert item['configuration_id'] is None and 'context_window' not in item
    assert [ref['configuration_id'] for ref in item['configuration_identities']] == [2, 3]


def test_revision_deterministic_and_changes_on_capability_identity_health_or_update(inventory):
    inventory.db.rows = [row(2, 7, name='z'), row(1, 1, name='a', shared=True)]
    before = inventory.module.get_routing_models(7, 42)
    inventory.db.rows.reverse()
    assert inventory.module.get_routing_models(7, 42) == before
    for field, value in [('updated_at', '2026-09-16T01:00:00'), ('uuid', 'recreated'), ('status_ok', False)]:
        original = inventory.db.rows[1][field]
        inventory.db.rows[1][field] = value
        assert inventory.module.get_routing_models(7, 42)['revision'] != before['revision']
        inventory.db.rows[1][field] = original
    inventory.db.rows[1]['data']['context_window'] = 16000
    after = inventory.module.get_routing_models(7, 42)
    assert after['revision'] != before['revision']
    assert after['items'][1]['configuration_fingerprint'] != before['items'][1]['configuration_fingerprint']


def test_no_credentials_or_raw_configuration_data_selected_or_returned(inventory):
    inventory.db.rows = [row(1, 7)]
    snapshot = inventory.module.get_routing_models(7, 42)
    assert 'DO-NOT-READ' not in json.dumps(snapshot)
    assert 'ai_credentials' not in json.dumps(snapshot)
    assert all('data' not in fields and 'ai_credentials' not in fields for fields in inventory.db.columns)


@pytest.mark.parametrize('project,user', [(7, None), (7, 0), (7, True), (7, '42'), (False, 42), (0, 42)])
def test_explicit_trusted_identity_required_before_query(inventory, project, user):
    with pytest.raises(ValueError, match='trusted project and user'):
        inventory.module.get_routing_models(project, user)
    assert inventory.db.sessions == [] and inventory.actor_calls == []


def test_inventory_size_fails_closed_instead_of_silently_truncating(inventory, monkeypatch):
    monkeypatch.setattr(inventory.module, 'MAX_ROUTING_CONFIGURATION_ROWS', 2)
    inventory.db.rows = [row(n, 7, name=f'model-{n}') for n in range(1, 4)]
    with pytest.raises(ValueError, match='row limit'):
        inventory.module.get_routing_models(7, 42)


def test_folder_lookup_failure_propagates(inventory, monkeypatch):
    def denied(*args, **kwargs):
        raise RuntimeError('visibility unavailable')
    monkeypatch.setattr(inventory.module, 'folder_exclusion_clause', denied)
    with pytest.raises(RuntimeError, match='visibility unavailable'):
        inventory.module.get_routing_models(7, 42)
    assert inventory.db.sessions == []
