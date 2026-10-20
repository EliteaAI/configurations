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
    uuid, id, project_id, type, section, generation = (
        _Column(n) for n in ('uuid', 'id', 'project_id', 'type', 'section', 'generation'))


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
    _stub('models.pd.registry', CONFIG_TYPE_REGISTRY={
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


def _row(project_id=PROJECT, type_='api_cred', section='credentials', generation=3, data=None, id_=11):
    row = SimpleNamespace(
        id=id_, uuid=uuidlib.uuid4(), project_id=project_id, type=type_, section=section, generation=generation,
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
                                      'generation': 3}}
    assert str(other.uuid) not in result
    assert 'data' not in result[str(row.uuid)] and FIXTURE_SECRET not in repr(result)


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


def _filled(properties):
    """A stored configuration with every field set, built without the code under test."""
    data = {}
    for name, schema in properties.items():
        if schema.get('format') == 'password' or any(
                isinstance(option, dict) and option.get('format') == 'password' for option in schema.get('anyOf', [])):
            data[name] = f'fixture-{name}'
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
        assert sorted(fields[name]) == sorted(bundle['fields']), name
        nested += any('.' in path for path in fields[name])
    assert nested == 3  # api_cred, nested, shallow; the chain of 8 is beyond the walker's depth bound


def test_secret_fields_stop_where_the_bundle_walker_stops(methods, monkeypatch):
    schema = _deep_schema(8)
    monkeypatch.setitem(methods.CONFIG_TYPE_REGISTRY, 'deep', SimpleNamespace(config_schema=schema))
    assert methods.Method.configuration_secret_fields(Self(), ['deep']) == {'deep': []}
    shallow = _deep_schema(2)
    monkeypatch.setitem(methods.CONFIG_TYPE_REGISTRY, 'shallow', SimpleNamespace(config_schema=shallow))
    assert methods.Method.configuration_secret_fields(Self(), ['shallow']) == {'shallow': ['a.child.child.deep']}
