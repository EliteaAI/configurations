"""C-1009 (#6757): the two in-process reads credential onboarding uses - metadata only, and the secret bundle."""
import importlib.util
import pathlib
import sys
import types
import uuid as uuidlib
from types import SimpleNamespace

import pytest
from pydantic import SecretStr

ROOT = pathlib.Path(__file__).resolve().parents[2]
PACKAGE = 'configurations_6757_test'
PROJECT, OTHER_PROJECT = 7, 8
FIXTURE_SECRET = 'fixture-secret-value'  # fake, test only
REGISTRY_VERSION = [1]  # the stub registry's change counter; a test bumps it as register_config_type does


class _Column:
    """Stands in for an ORM column: comparisons become tuples the fake query evaluates."""

    def __init__(self, name):
        self.name = name

    def __eq__(self, other):
        return ('eq', self.name, other)

    def __hash__(self):
        return hash(self.name)

    def in_(self, values):
        return ('in', self.name, list(values))


class _ConfigurationModel:
    uuid, id, project_id, type, section, generation, elitea_title = (
        _Column(n) for n in ('uuid', 'id', 'project_id', 'type', 'section', 'generation', 'elitea_title'))


class _Query:
    def __init__(self, rows):
        self.rows = rows

    def filter(self, *conditions):
        def keep(row):
            for op, name, value in conditions:
                actual = getattr(row, name)
                if (op == 'eq' and actual != value) or (op == 'in' and actual not in value):
                    return False
            return True
        return _Query([row for row in self.rows if keep(row)])

    def all(self):
        return self.rows

    def first(self):
        return self.rows[0] if self.rows else None


class _Session:
    def __init__(self, rows):
        self.rows = rows

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def query(self, *_columns):
        return _Query(self.rows)


class _Vault:
    """unsecret resolves {{secret.NAME}} from a table; unknown references are left as they are."""

    table = {}

    def __init__(self, project_id):
        self.project_id = project_id

    def unsecret(self, value):
        return self.table.get(value, value)


class _Context:
    """A genuine-looking context: the check is on the class the secret_stores tool exposes."""


STATE = {'rows': {}}
CONFIG_SCHEMA = {
    'properties': {
        'url': {'type': 'string'},
        'api_key': {'type': 'string', 'format': 'password'},
        'token': {'anyOf': [{'type': 'string', 'format': 'password'}, {'type': 'null'}]},
        'oauth': {'type': 'object', 'properties': {
            'client_id': {'type': 'string'}, 'client_secret': {'type': 'string', 'format': 'password'}}},
    }
}


# the shape the SDK's OpenApiConfiguration.headers (Dict[str, SecretStr]) serializes to
HEADERS_MAP = {'type': 'object', 'additionalProperties': {'type': 'string', 'format': 'password', 'writeOnly': True}}


def _stub(name, **attrs):
    module = types.ModuleType(f'{PACKAGE}.{name}')
    module.__dict__.update(attrs)
    sys.modules[module.__name__] = module
    return module


def _load(name):
    spec = importlib.util.spec_from_file_location(f'{PACKAGE}.{name}', ROOT / (name.replace('.', '/') + '.py'))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope='module')
def methods():
    saved = {name: sys.modules.get(name) for name in ('tools', 'pylon.core.tools')}
    for suffix, path in [('', ROOT), ('.models', ROOT / 'models'), ('.models.pd', ROOT / 'models/pd'),
                         ('.methods', ROOT / 'methods')]:
        package = types.ModuleType(PACKAGE + suffix)
        package.__path__ = [str(path)]
        sys.modules[package.__name__] = package
    db = SimpleNamespace(get_session=lambda project_id: _Session(STATE['rows'].get(project_id, [])))
    _stub('local_tools', db=db, VaultClient=_Vault)
    _stub('models.configuration', Configuration=_ConfigurationModel)
    _stub('models.pd.registry', registry_version=lambda: REGISTRY_VERSION[0], CONFIG_TYPE_REGISTRY={
        'api_cred': SimpleNamespace(config_schema=CONFIG_SCHEMA),
        'plain': SimpleNamespace(config_schema={'properties': {'url': {'type': 'string'}}}),
        'no_schema': SimpleNamespace(config_schema=None),
    })
    from_utils = _stub('utils')
    from_utils._is_password_schema = lambda schema: schema.get('format') == 'password' or any(
        option.get('format') == 'password' for option in schema.get('anyOf', []) if isinstance(option, dict))
    web = SimpleNamespace(method=lambda *a, **k: (lambda func: func))
    tools_mod = types.ModuleType('pylon.core.tools')
    tools_mod.web = web
    sys.modules['pylon.core.tools'] = tools_mod
    module = _load('methods.credential_governance')
    yield module
    for name in [m for m in sys.modules if m.startswith(PACKAGE)]:
        del sys.modules[name]
    for name, original in saved.items():
        if original is not None:
            sys.modules[name] = original


@pytest.fixture
def stores(monkeypatch):
    """The secret_stores tool as shared registers it: the context class is the one identity that is checked."""
    class AuthorizedStoreContext:
        def __init__(self, purpose, project_id):
            self.purpose, self.project_id = purpose, project_id

    fake = SimpleNamespace(AuthorizedStoreContext=AuthorizedStoreContext)
    tools = sys.modules['tools']
    monkeypatch.setattr(tools, 'secret_stores', fake, raising=False)
    return fake


@pytest.fixture(autouse=True)
def _reset():
    STATE['rows'] = {}
    _Vault.table = {'{{secret.k1}}': FIXTURE_SECRET, '{{secret.k2}}': 'fixture-token', '{{secret.k3}}': 'fixture-oauth'}
    yield


def _row(project_id=PROJECT, type_='api_cred', section='credentials', generation=3, data=None, id_=11,
         title='github-pat'):
    row = SimpleNamespace(
        id=id_, uuid=uuidlib.uuid4(), project_id=project_id, type=type_, section=section, generation=generation, elitea_title=title,
        data=data if data is not None else {
            'url': 'https://example.invalid', 'api_key': '{{secret.k1}}',
            'oauth': {'client_id': 'public-id', 'client_secret': '{{secret.k3}}'}})
    STATE['rows'].setdefault(project_id, []).append(row)
    return row


class Self:  # the module the methods are bound to
    pass


# --- configuration_credential_facts ------------------------------------------------------------------------

def test_facts_are_metadata_only_and_keyed_by_uuid(methods):
    row = _row()
    other = _row(id_=12)
    result = methods.Method.configuration_credential_facts(Self(), PROJECT, [str(row.uuid)])
    assert result == {str(row.uuid): {'id': 11, 'project_id': PROJECT, 'type': 'api_cred', 'section': 'credentials',
                                      'generation': 3, 'title': 'github-pat'}}
    assert set(result[str(row.uuid)]) == {'id', 'project_id', 'type', 'section', 'generation', 'title'}
    assert str(other.uuid) not in result
    assert 'data' not in result[str(row.uuid)] and FIXTURE_SECRET not in repr(result)


def test_facts_title_is_none_when_the_configuration_has_none(methods):
    row = _row(title=None)
    result = methods.Method.configuration_credential_facts(Self(), PROJECT, [str(row.uuid)])
    assert result[str(row.uuid)]['title'] is None


def test_facts_for_unknown_uuid_and_empty_list(methods):
    assert methods.Method.configuration_credential_facts(Self(), PROJECT, [str(uuidlib.uuid4())]) == {}
    assert methods.Method.configuration_credential_facts(Self(), PROJECT, []) == {}


def test_facts_reads_the_given_projects_schema_only(methods):
    row = _row(project_id=OTHER_PROJECT)
    assert methods.Method.configuration_credential_facts(Self(), PROJECT, [str(row.uuid)]) == {}


def test_facts_are_bounded_and_strict_about_uuids(methods):
    with pytest.raises(ValueError):
        methods.Method.configuration_credential_facts(Self(), PROJECT, [str(uuidlib.uuid4())] * 65)
    with pytest.raises(ValueError):
        methods.Method.configuration_credential_facts(Self(), PROJECT, ['not-a-uuid'])
    with pytest.raises(ValueError):
        methods.Method.configuration_credential_facts(Self(), PROJECT, 'a-string')
    assert methods.Method.configuration_credential_facts(Self(), PROJECT, [str(uuidlib.uuid4())] * 64) == {}


# --- configuration_secret_bundle: who may ask ------------------------------------------------------------------

@pytest.mark.parametrize('purpose', ['onboard', 'probe'])
def test_bundle_answers_onboarding_and_probe_contexts(methods, stores, purpose):
    row = _row()
    result = methods.Method.configuration_secret_bundle(
        Self(), PROJECT, row.id, stores.AuthorizedStoreContext(purpose, PROJECT))
    assert result['configuration_generation'] == 3
    assert set(result['fields']) == {'api_key', 'oauth.client_secret'}


@pytest.mark.parametrize('purpose', ['retire', 'reconcile', 'smoke', 'release', '', None])
def test_bundle_refuses_every_other_purpose(methods, stores, purpose):
    row = _row()
    with pytest.raises(PermissionError):
        methods.Method.configuration_secret_bundle(
            Self(), PROJECT, row.id, stores.AuthorizedStoreContext(purpose, PROJECT))


def test_bundle_refuses_a_context_for_another_project(methods, stores):
    row = _row()
    with pytest.raises(PermissionError):
        methods.Method.configuration_secret_bundle(
            Self(), PROJECT, row.id, stores.AuthorizedStoreContext('onboard', OTHER_PROJECT))


@pytest.mark.parametrize('forged', [None, {'purpose': 'onboard', 'project_id': PROJECT}, 'onboard',
                                    SimpleNamespace(purpose='onboard', project_id=PROJECT)])
def test_bundle_refuses_anything_that_is_not_a_store_context(methods, stores, forged):
    row = _row()
    with pytest.raises(PermissionError):
        methods.Method.configuration_secret_bundle(Self(), PROJECT, row.id, forged)


def test_bundle_never_reads_the_row_before_the_context_is_checked(methods, stores):
    STATE['rows'] = None  # any read would fail with TypeError, not PermissionError
    with pytest.raises(PermissionError):
        methods.Method.configuration_secret_bundle(Self(), PROJECT, 1, SimpleNamespace(purpose='onboard'))


# --- configuration_secret_bundle: what it returns ----------------------------------------------------------------

def test_bundle_has_only_password_fields_as_secretstr_with_the_generation(methods, stores):
    row = _row(generation=9)
    result = methods.Method.configuration_secret_bundle(
        Self(), PROJECT, row.id, stores.AuthorizedStoreContext('onboard', PROJECT))
    assert result['configuration_generation'] == 9
    assert all(isinstance(value, SecretStr) for value in result['fields'].values())
    assert result['fields']['api_key'].get_secret_value() == FIXTURE_SECRET
    assert result['fields']['oauth.client_secret'].get_secret_value() == 'fixture-oauth'
    assert FIXTURE_SECRET not in repr(result) and FIXTURE_SECRET not in str(result)


def test_bundle_resolves_anyof_password_fields_and_skips_empty_ones(methods, stores):
    row = _row(data={'url': 'u', 'api_key': '', 'token': '{{secret.k2}}'})
    result = methods.Method.configuration_secret_bundle(
        Self(), PROJECT, row.id, stores.AuthorizedStoreContext('onboard', PROJECT))
    assert set(result['fields']) == {'token'}


def test_bundle_does_not_change_the_stored_row(methods, stores):
    row = _row()
    before = {'url': row.data['url'], 'api_key': row.data['api_key']}
    methods.Method.configuration_secret_bundle(Self(), PROJECT, row.id, stores.AuthorizedStoreContext('onboard', PROJECT))
    assert row.data['api_key'] == before['api_key'] and row.data['url'] == before['url']


def test_bundle_of_a_missing_configuration_is_none(methods, stores):
    assert methods.Method.configuration_secret_bundle(
        Self(), PROJECT, 999, stores.AuthorizedStoreContext('onboard', PROJECT)) is None


def test_bundle_cannot_read_a_row_of_another_project(methods, stores):
    row = _row(project_id=OTHER_PROJECT)
    assert methods.Method.configuration_secret_bundle(
        Self(), PROJECT, row.id, stores.AuthorizedStoreContext('onboard', PROJECT)) is None


@pytest.mark.parametrize('type_, data', [
    ('plain', {'url': 'u'}), ('no_schema', {'x': '{{secret.k1}}'}), ('unregistered', {'api_key': '{{secret.k1}}'}),
    ('api_cred', {'url': 'u'}),
])
def test_bundle_without_secret_fields_is_a_fixed_error(methods, stores, type_, data):
    row = _row(type_=type_, data=data)
    with pytest.raises(ValueError, match='^NO_SECRET_FIELDS$'):
        methods.Method.configuration_secret_bundle(
            Self(), PROJECT, row.id, stores.AuthorizedStoreContext('onboard', PROJECT))


def test_bundle_with_an_unresolvable_reference_is_a_fixed_error_and_never_partial(methods, stores):
    row = _row(data={'api_key': '{{secret.k1}}', 'token': '{{secret.missing}}'})
    with pytest.raises(ValueError) as caught:
        methods.Method.configuration_secret_bundle(
            Self(), PROJECT, row.id, stores.AuthorizedStoreContext('onboard', PROJECT))
    assert str(caught.value) == 'SECRET_UNRESOLVED' and FIXTURE_SECRET not in repr(caught.value)


def test_bundle_with_a_non_string_secret_value_is_unresolved(methods, stores):
    row = _row(data={'api_key': 12345})
    with pytest.raises(ValueError, match='^SECRET_UNRESOLVED$'):
        methods.Method.configuration_secret_bundle(
            Self(), PROJECT, row.id, stores.AuthorizedStoreContext('onboard', PROJECT))


def test_nested_secrets_are_bounded_by_depth(methods):
    schema = {'type': 'object', 'properties': {}}
    node = schema
    for level in range(8):
        node['properties']['child'] = {'type': 'object', 'properties': {}}
        node = node['properties']['child']
    node['properties']['deep'] = {'type': 'string', 'format': 'password'}
    data = {'a': {}}
    cursor = data['a']
    for level in range(8):
        cursor['child'] = {}
        cursor = cursor['child']
    cursor['deep'] = '{{secret.k1}}'
    fields = methods.collect_secret_fields({'a': schema}, data, _Vault(1).unsecret)
    assert fields == {}


# --- configuration_secret_fields (#6946) -------------------------------------------------------------------

def _deep_schema(levels):
    """An object chain ``a.child.child...`` with a password at the bottom, and the same chain with a public
    sibling at every level."""
    node = {'type': 'object', 'properties': {'public': {'type': 'string'}}}
    top = {'properties': {'a': node}}
    for _ in range(levels):
        child = {'type': 'object', 'properties': {'public': {'type': 'string'}}}
        node['properties']['child'] = child
        node = child
    node['properties']['deep'] = {'type': 'string', 'format': 'password'}
    return top


def _is_password(schema):
    return schema.get('format') == 'password' or any(
        isinstance(option, dict) and option.get('format') == 'password' for option in schema.get('anyOf', []))


def _filled(properties):
    """A stored configuration with every field set, built without the code under test."""
    data = {}
    for name, schema in properties.items():
        options = [schema, *[option for option in schema.get('anyOf', []) if isinstance(option, dict)]]
        if _is_password(schema):
            data[name] = f'fixture-{name}'
        elif any(isinstance(option.get('additionalProperties'), dict) and _is_password(option['additionalProperties'])
                 for option in options):
            data[name] = {'X-First': f'fixture-{name}-1', 'X-Second': f'fixture-{name}-2'}
        elif any(isinstance(option.get('items'), dict) and _is_password(option['items']) for option in options):
            data[name] = [f'fixture-{name}-1', f'fixture-{name}-2']
        elif isinstance(schema.get('properties'), dict):
            data[name] = _filled(schema['properties'])
        else:
            data[name] = 'public-value'
    return data


def _registered_schemas():
    """Every kind of schema the registry holds: the stub types, nested and anyOf passwords, a chain deeper than
    the walker goes, and the schemas the SDK registers (``configurations_register`` receives these)."""
    schemas = {
        'api_cred': CONFIG_SCHEMA, 'plain': {'properties': {'url': {'type': 'string'}}}, 'no_schema': {},
        'nested': {'properties': {'a': {'type': 'object', 'properties': {'b': {'type': 'object', 'properties': {
            'c': {'type': 'string', 'format': 'password'}, 'd': {'anyOf': [
                {'type': 'string', 'format': 'password'}, {'type': 'null'}]}}}, 'e': {'type': 'string'}}}}},
        'deep': _deep_schema(8), 'shallow': _deep_schema(2),
        'headers_map': {'properties': {'base_url': {'type': 'string'}, 'headers': HEADERS_MAP}},
        'optional_map': {'properties': {'headers': {'anyOf': [HEADERS_MAP, {'type': 'null'}]}}},
        'nested_map': {'properties': {'auth': {'type': 'object', 'properties': {
            'user': {'type': 'string'}, 'extra': HEADERS_MAP}}}},
        'password_array': {'properties': {'tokens': {'type': 'array', 'items': {'type': 'string', 'format': 'password'}}}},
    }
    sdk = pytest.importorskip('elitea_sdk.configurations')
    schemas.update({f'sdk_{name}': schema for name, schema in sdk.AVAILABLE_CONFIGURATIONS.items()})
    return schemas


def test_secret_fields_are_the_password_paths_of_the_registered_schema_in_schema_order(methods):
    fields = methods.Method.configuration_secret_fields(Self(), ['api_cred', 'plain', 'no_schema'])
    assert fields == {'api_cred': ['api_key', 'token', 'oauth.client_secret'], 'plain': [], 'no_schema': []}


def test_secret_fields_leave_out_unregistered_types_and_answer_only_what_was_asked(methods):
    assert methods.Method.configuration_secret_fields(Self(), ['api_cred', 'unknown', 'api_cred']) == {
        'api_cred': ['api_key', 'token', 'oauth.client_secret']}
    assert methods.Method.configuration_secret_fields(Self(), []) == {}
    assert methods.Method.configuration_secret_fields(Self(), {'plain'}) == {'plain': []}


def test_secret_fields_read_no_row_and_resolve_nothing(methods, monkeypatch):
    def forbidden(*_a, **_k):
        raise AssertionError('no database and no vault')

    monkeypatch.setattr(methods, 'db', SimpleNamespace(get_session=forbidden))
    monkeypatch.setattr(methods, 'VaultClient', forbidden)
    assert methods.Method.configuration_secret_fields(Self(), ['api_cred'])['api_cred'][0] == 'api_key'


@pytest.mark.parametrize('bad', ['api_cred', None, [1], ['api_cred', None], {'api_cred': 1}, ['t'] * 257])
def test_secret_fields_are_bounded_and_strict_about_the_names(methods, bad):
    with pytest.raises(ValueError):
        methods.Method.configuration_secret_fields(Self(), bad)


def _expand(paths, data):
    """The field names a configuration with ``data`` has under ``paths``: a ``<path>.*`` wildcard is every key of
    the dict (every index of the list) at ``<path>``. Written independently of the code under test."""
    names = []
    for path in paths:
        if not path.endswith('.*'):
            names.append(path)
            continue
        node = data
        for part in path[:-2].split('.'):
            node = node[part]
        names.extend(f'{path[:-1]}{key}' for key in (node if isinstance(node, dict) else range(len(node))))
    return names


def test_secret_fields_equal_what_the_bundle_returns_for_every_registered_type(methods, stores, monkeypatch):
    schemas = _registered_schemas()
    assert len(schemas) > 30
    for name, schema in schemas.items():
        monkeypatch.setitem(methods.CONFIG_TYPE_REGISTRY, name, SimpleNamespace(config_schema=schema or None))
    fields = methods.Method.configuration_secret_fields(Self(), list(schemas))
    assert set(fields) == set(schemas)
    nested = 0
    for name, schema in schemas.items():
        STATE['rows'] = {}
        row = _row(type_=name, data=_filled(schema.get('properties') or {}))
        try:
            bundle = methods.Method.configuration_secret_bundle(
                Self(), PROJECT, row.id, stores.AuthorizedStoreContext('onboard', PROJECT))
        except ValueError as exc:
            assert str(exc) == 'NO_SECRET_FIELDS' and fields[name] == [], name
            continue
        assert sorted(_expand(fields[name], row.data)) == sorted(bundle['fields']), name
        nested += any('.' in path for path in fields[name])
    # api_cred, nested, shallow, the SDK's openapi (headers.*) and the four map/array schemas; the chain of 8 is
    # beyond the walker's depth bound
    assert nested == 8


def test_secret_fields_stop_where_the_bundle_walker_stops(methods, monkeypatch):
    schema = _deep_schema(8)
    monkeypatch.setitem(methods.CONFIG_TYPE_REGISTRY, 'deep', SimpleNamespace(config_schema=schema))
    assert methods.Method.configuration_secret_fields(Self(), ['deep']) == {'deep': []}
    shallow = _deep_schema(2)
    monkeypatch.setitem(methods.CONFIG_TYPE_REGISTRY, 'shallow', SimpleNamespace(config_schema=shallow))
    assert methods.Method.configuration_secret_fields(Self(), ['shallow']) == {'shallow': ['a.child.child.deep']}


# --- maps and arrays of passwords (#6946) ------------------------------------------------------------------

MAP_SCHEMAS = {
    'headers_map': {'properties': {'base_url': {'type': 'string'}, 'headers': HEADERS_MAP}},
    'optional_map': {'properties': {'headers': {'anyOf': [HEADERS_MAP, {'type': 'null'}]}}},
    'nested_map': {'properties': {'auth': {'type': 'object', 'properties': {
        'user': {'type': 'string'}, 'extra': HEADERS_MAP}}}},
    'password_array': {'properties': {'tokens': {'type': 'array', 'items': {'type': 'string', 'format': 'password'}}}},
    'mixed': {'properties': {'api_key': {'type': 'string', 'format': 'password'}, 'headers': HEADERS_MAP}},
}


@pytest.fixture
def map_types(methods, monkeypatch):
    for name, schema in MAP_SCHEMAS.items():
        monkeypatch.setitem(methods.CONFIG_TYPE_REGISTRY, name, SimpleNamespace(config_schema=schema))


def test_secret_fields_name_a_map_or_array_of_passwords_with_a_wildcard(methods, map_types):
    assert methods.Method.configuration_secret_fields(Self(), list(MAP_SCHEMAS)) == {
        'headers_map': ['headers.*'], 'optional_map': ['headers.*'], 'nested_map': ['auth.extra.*'],
        'password_array': ['tokens.*'], 'mixed': ['api_key', 'headers.*']}


def _bundle(methods, stores, type_, data):
    row = _row(type_=type_, data=data)
    return methods.Method.configuration_secret_bundle(
        Self(), PROJECT, row.id, stores.AuthorizedStoreContext('onboard', PROJECT))


def test_bundle_names_every_map_entry_present_and_resolves_references(methods, stores, map_types):
    result = _bundle(methods, stores, 'mixed', {
        'api_key': '{{secret.k1}}', 'headers': {'X-API-Key': '{{secret.k2}}', 'X-Tenant-ID': 'literal-tenant'}})
    assert set(result['fields']) == {'api_key', 'headers.X-API-Key', 'headers.X-Tenant-ID'}
    assert all(isinstance(value, SecretStr) for value in result['fields'].values())
    assert result['fields']['headers.X-API-Key'].get_secret_value() == 'fixture-token'
    assert result['fields']['headers.X-Tenant-ID'].get_secret_value() == 'literal-tenant'
    assert 'fixture-token' not in repr(result)


@pytest.mark.parametrize('type_, data, expected', [
    ('optional_map', {'headers': {'X-A': 'a-value'}}, {'headers.X-A'}),
    ('optional_map', {'headers': None}, set()),
    ('nested_map', {'auth': {'user': 'u', 'extra': {'X-B': 'b-value'}}}, {'auth.extra.X-B'}),
    ('password_array', {'tokens': ['first', '', 'third']}, {'tokens.0', 'tokens.2'}),
    ('headers_map', {'headers': {'X-Empty': '', 'X-None': None, 'X-Set': 'set-value'}}, {'headers.X-Set'}),
])
def test_bundle_map_and_array_entries(methods, stores, map_types, type_, data, expected):
    if not expected:
        with pytest.raises(ValueError, match='^NO_SECRET_FIELDS$'):
            _bundle(methods, stores, type_, data)
    else:
        assert set(_bundle(methods, stores, type_, data)['fields']) == expected


@pytest.mark.parametrize('headers', [{}, 'not-a-dict', 7])
def test_bundle_of_an_empty_or_malformed_map_has_no_fields(methods, stores, map_types, headers):
    with pytest.raises(ValueError, match='^NO_SECRET_FIELDS$'):
        _bundle(methods, stores, 'headers_map', {'headers': headers})


@pytest.mark.parametrize('entry', ['{{secret.missing}}', 12345, {'nested': 'x'}])
def test_bundle_with_an_unresolved_map_entry_is_a_fixed_error_and_never_partial(methods, stores, map_types, entry):
    with pytest.raises(ValueError) as caught:
        _bundle(methods, stores, 'mixed', {'api_key': '{{secret.k1}}', 'headers': {'X-Good': 'ok', 'X-Bad': entry}})
    assert str(caught.value) == 'SECRET_UNRESOLVED' and FIXTURE_SECRET not in repr(caught.value)


# --- the secret-path cache (S2, perf review 2026-10-21) --------------------------------------------------------

@pytest.fixture
def walks(methods, monkeypatch):
    """Counts the schema walks and starts every test from an empty cache."""
    calls = []
    real = methods.secret_field_paths
    monkeypatch.setattr(methods, 'secret_field_paths', lambda properties: calls.append(properties) or real(properties))
    methods._PATHS_CACHE.clear()
    return calls


def test_two_calls_for_the_same_types_walk_each_schema_once(methods, walks):
    first = methods.Method.configuration_secret_fields(Self(), ['api_cred', 'plain', 'no_schema'])
    second = methods.Method.configuration_secret_fields(Self(), ['api_cred', 'plain', 'no_schema'])
    assert first == second == {
        'api_cred': ['api_key', 'token', 'oauth.client_secret'], 'plain': [], 'no_schema': []}
    assert len(walks) == 3  # one per type, not six
    for _ in range(100):
        methods.Method.configuration_secret_fields(Self(), ['api_cred'])
    assert len(walks) == 3


def test_a_cached_answer_cannot_be_changed_by_the_caller(methods, walks):
    methods.Method.configuration_secret_fields(Self(), ['api_cred'])['api_cred'].append('injected')
    assert 'injected' not in methods.Method.configuration_secret_fields(Self(), ['api_cred'])['api_cred']


def test_a_registry_change_walks_again(methods, walks):
    methods.Method.configuration_secret_fields(Self(), ['api_cred'])
    REGISTRY_VERSION[0] += 1
    methods.Method.configuration_secret_fields(Self(), ['api_cred'])
    assert len(walks) == 2
    assert methods.Method.configuration_registry_version(Self()) == REGISTRY_VERSION[0]


def test_a_replaced_entry_is_not_answered_from_the_old_schema(methods, walks, monkeypatch):
    schema = {'properties': {'a': {'type': 'string', 'format': 'password'}}}
    monkeypatch.setitem(methods.CONFIG_TYPE_REGISTRY, 'swapped', SimpleNamespace(config_schema=schema))
    assert methods.Method.configuration_secret_fields(Self(), ['swapped']) == {'swapped': ['a']}
    monkeypatch.setitem(methods.CONFIG_TYPE_REGISTRY, 'swapped', SimpleNamespace(config_schema={'properties': {}}))
    assert methods.Method.configuration_secret_fields(Self(), ['swapped']) == {'swapped': []}  # same version, new entry
    assert len(walks) == 2


def test_an_unregistered_type_is_left_out_and_not_cached(methods, walks, monkeypatch):
    assert methods.Method.configuration_secret_fields(Self(), ['gone']) == {}
    monkeypatch.setitem(methods.CONFIG_TYPE_REGISTRY, 'gone', SimpleNamespace(config_schema={'properties': {
        'k': {'type': 'string', 'format': 'password'}}}))
    assert methods.Method.configuration_secret_fields(Self(), ['gone']) == {'gone': ['k']}
    monkeypatch.delitem(methods.CONFIG_TYPE_REGISTRY, 'gone')
    assert methods.Method.configuration_secret_fields(Self(), ['gone']) == {}
    assert 'gone' not in methods._PATHS_CACHE
