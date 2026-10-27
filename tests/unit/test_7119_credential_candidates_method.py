"""C-1009 (#7119): ``configuration_credential_list``, the metadata-only candidate list for credential references.

Runs the real method and its real SQL against an in-memory SQLite table that has the same columns as the
``configuration`` table (including ``data``), so the filters, paging and escaping are exercised, not mocked.
"""
import datetime
import importlib.util
import json
import pathlib
import sys
import types
import uuid as uuidlib
from types import SimpleNamespace

import pytest
from sqlalchemy import JSON, UUID, Boolean, DateTime, Integer, String, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

ROOT = pathlib.Path(__file__).resolve().parents[2]
PACKAGE = 'configurations_7119_method_test'
PROJECT, OTHER_PROJECT, BIG_PROJECT = 7, 8, 9
FIXTURE_SECRET = 'fixture-secret-value'  # fake, test only
REGISTRY_VERSION = [1]  # the stub registry's change counter; a test bumps it as register_config_type does
FIXTURE_REFERENCE = '{{secret.fixture_key}}'
ROW_KEYS = {
    'configuration_id', 'uuid', 'configuration_type', 'title', 'label', 'section', 'generation', 'shared',
    'author_id', 'created_at', 'has_secret_fields',
}
HEADERS_MAP = {'type': 'object', 'additionalProperties': {'type': 'string', 'format': 'password', 'writeOnly': True}}
CONFIG_SCHEMA = {
    'properties': {
        'url': {'type': 'string'},
        'api_key': {'type': 'string', 'format': 'password'},
        'oauth': {'type': 'object', 'properties': {
            'client_id': {'type': 'string'}, 'client_secret': {'type': 'string', 'format': 'password'}}},
        'headers': HEADERS_MAP,
    }
}


class _Base(DeclarativeBase):
    pass


class _Configuration(_Base):
    __tablename__ = 'configuration'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    uuid: Mapped[uuidlib.UUID] = mapped_column(UUID(as_uuid=True), unique=True, default=uuidlib.uuid4)
    generation: Mapped[int] = mapped_column(Integer, default=1)
    project_id: Mapped[int] = mapped_column(Integer)
    label: Mapped[str] = mapped_column(String, nullable=True)
    elitea_title: Mapped[str] = mapped_column(String, unique=True)
    type: Mapped[str] = mapped_column(String)
    section: Mapped[str] = mapped_column(String)
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    shared: Mapped[bool] = mapped_column(Boolean, default=False)
    author_id: Mapped[int] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime)


ENGINE = create_engine('sqlite://', connect_args={'check_same_thread': False})
_Base.metadata.create_all(ENGINE)
STATEMENTS = []
SESSION_PROJECTS = []


@event.listens_for(ENGINE, 'before_cursor_execute')
def _record(conn, cursor, statement, parameters, context, executemany):  # pylint: disable=R0913,W0613
    STATEMENTS.append(statement)


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


def _get_session(project_id):
    SESSION_PROJECTS.append(project_id)
    return Session(ENGINE)


@pytest.fixture(scope='module')
def credential_facts():
    saved = {name: sys.modules.get(name) for name in ('tools', 'pylon.core.tools')}
    for suffix, path in [('', ROOT), ('.models', ROOT / 'models'), ('.models.pd', ROOT / 'models/pd'),
                         ('.methods', ROOT / 'methods')]:
        package = types.ModuleType(PACKAGE + suffix)
        package.__path__ = [str(path)]
        sys.modules[package.__name__] = package
    _stub('local_tools', db=SimpleNamespace(get_session=_get_session), VaultClient=object)
    _stub('models.configuration', Configuration=_Configuration)
    _stub('models.pd.registry', registry_version=lambda: REGISTRY_VERSION[0], owned_entry=lambda *_: None, CONFIG_TYPE_REGISTRY={
        'api_cred': SimpleNamespace(config_schema=CONFIG_SCHEMA),
        'plain': SimpleNamespace(config_schema={'properties': {'url': {'type': 'string'}}}),
        'no_schema': SimpleNamespace(config_schema=None),
    })
    from_utils = _stub('utils')
    from_utils._is_password_schema = lambda schema: schema.get('format') == 'password' or any(
        option.get('format') == 'password' for option in schema.get('anyOf', []) if isinstance(option, dict))
    tools_mod = types.ModuleType('pylon.core.tools')
    tools_mod.web = SimpleNamespace(method=lambda *a, **k: (lambda func: func))
    tools_mod.log = SimpleNamespace(info=print, warning=print, error=print, debug=print)
    sys.modules['pylon.core.tools'] = tools_mod
    _load('governed_probe')
    _load('methods.credential_governance')
    module = _load('methods.credential_facts')
    yield module
    for name in [m for m in sys.modules if m.startswith(PACKAGE)]:
        del sys.modules[name]
    for name, original in saved.items():
        if original is not None:
            sys.modules[name] = original


class Self:  # the module the method is bound to
    pass


_COUNTER = iter(range(1, 10_000))


def _add(project_id=PROJECT, title=None, label=None, type_='api_cred', section='credentials', generation=1,
         minutes=0, data=None):
    n = next(_COUNTER)
    row = _Configuration(
        uuid=uuidlib.uuid4(), generation=generation, project_id=project_id, label=label,
        elitea_title=title or f'cfg-{n}', type=type_, section=section, shared=False, author_id=5,
        created_at=datetime.datetime(2026, 1, 1) + datetime.timedelta(minutes=minutes),
        data=data if data is not None else {
            'url': 'https://example.invalid', 'api_key': FIXTURE_REFERENCE, 'plain_leak': FIXTURE_SECRET,
            'oauth': {'client_id': 'public-id', 'client_secret': FIXTURE_SECRET},
            'headers': {'X-Key': FIXTURE_SECRET}},
    )
    with Session(ENGINE) as session:
        session.add(row)
        session.commit()
        return row.id, str(row.uuid)


@pytest.fixture(autouse=True)
def _reset():
    with Session(ENGINE) as session:
        session.query(_Configuration).delete()
        session.commit()
    STATEMENTS.clear()
    SESSION_PROJECTS.clear()
    yield


def _list(module, *args, **kwargs):
    return module.Method.configuration_credential_list(Self(), *args, **kwargs)


# --- section and project scope -----------------------------------------------------------------------------

def test_lists_only_the_credentials_section_of_the_project(credential_facts):
    _, wanted = _add(title='github-pat')
    _add(title='llm-model', type_='llm_model', section='llm')
    _add(title='vector', type_='pgvector', section='vector')
    _add(title='other-project-cred', project_id=OTHER_PROJECT)
    result = _list(credential_facts, PROJECT)
    assert result['total'] == 1
    assert [row['uuid'] for row in result['rows']] == [wanted]
    assert {row['section'] for row in result['rows']} == {'credentials'}
    assert SESSION_PROJECTS == [PROJECT]


def test_unknown_project_is_empty_not_an_error(credential_facts):
    _add()
    assert _list(credential_facts, 99999) == {'total': 0, 'rows': []}


@pytest.mark.parametrize('section', ['llm', 'embedding', 'Credentials', '', 'credentials%', None])
def test_other_sections_are_refused(credential_facts, section):
    _add(section='llm', type_='llm_model')
    with pytest.raises(ValueError, match='INVALID_SECTION'):
        _list(credential_facts, PROJECT, section=section)


# --- no secret ever leaves ---------------------------------------------------------------------------------

def test_a_row_has_exactly_the_metadata_keys_and_no_secret_field(credential_facts):
    _add(title='with-secrets', type_='api_cred')
    result = _list(credential_facts, PROJECT)
    (row,) = result['rows']
    assert set(row) == ROW_KEYS
    secret_paths = {path.split('.')[0] for path in credential_facts.secret_field_paths(CONFIG_SCHEMA['properties'])}
    assert secret_paths == {'api_key', 'oauth', 'headers'}  # the walker sees the type's secret fields
    assert not (set(row) & {'data', 'api_key', 'oauth', 'headers', 'client_secret', 'plain_leak'})
    text = json.dumps(result)
    assert FIXTURE_SECRET not in text
    assert '{{secret.' not in text and FIXTURE_REFERENCE not in text


def test_the_data_column_is_never_selected(credential_facts):
    _add()
    STATEMENTS.clear()
    _list(credential_facts, PROJECT, search='cfg')
    selects = [s for s in STATEMENTS if s.lstrip().upper().startswith('SELECT')]
    assert selects
    for statement in selects:
        assert 'configuration.data' not in statement.lower()


def test_has_secret_fields_comes_from_the_type_schema(credential_facts):
    _add(title='a', type_='api_cred')
    _add(title='b', type_='plain')
    _add(title='c', type_='no_schema')
    _add(title='d', type_='gone_from_registry')
    by_title = {row['title']: row['has_secret_fields'] for row in _list(credential_facts, PROJECT)['rows']}
    assert by_title == {'a': True, 'b': False, 'c': False, 'd': False}


def test_a_stored_secret_in_a_plain_type_is_still_not_returned(credential_facts):
    _add(title='plain-with-leak', type_='plain')
    assert FIXTURE_SECRET not in json.dumps(_list(credential_facts, PROJECT))


# --- paging ------------------------------------------------------------------------------------------------

def test_default_page_is_twenty_and_newest_first(credential_facts):
    for minute in range(30):
        _add(title=f't{minute:02d}', minutes=minute)
    result = _list(credential_facts, PROJECT)
    assert result['total'] == 30
    assert [row['title'] for row in result['rows']] == [f't{m:02d}' for m in range(29, 9, -1)]


def test_limit_is_capped_at_one_hundred(credential_facts):
    for minute in range(120):
        _add(project_id=BIG_PROJECT, minutes=minute)
    result = _list(credential_facts, BIG_PROJECT, limit=1000)
    assert result['total'] == 120
    assert len(result['rows']) == credential_facts.MAX_LIST_LIMIT == 100


def test_offset_pages_do_not_overlap_and_cover_everything(credential_facts):
    for minute in range(25):
        _add(title=f'p{minute:02d}', minutes=minute % 3)  # ties on created_at: the id breaks them
    seen = []
    for offset in range(0, 30, 10):
        seen += [row['title'] for row in _list(credential_facts, PROJECT, limit=10, offset=offset)['rows']]
    assert len(seen) == len(set(seen)) == 25


def test_offset_past_the_end_is_empty_but_keeps_the_total(credential_facts):
    _add()
    assert _list(credential_facts, PROJECT, offset=50) == {'total': 1, 'rows': []}


@pytest.mark.parametrize('limit', [0, -1, '5', 2.5, True, None])
def test_bad_limit_is_refused(credential_facts, limit):
    with pytest.raises(ValueError, match='INVALID_LIMIT'):
        _list(credential_facts, PROJECT, limit=limit)


@pytest.mark.parametrize('offset', [-1, '0', 1.5, True, None])
def test_bad_offset_is_refused(credential_facts, offset):
    with pytest.raises(ValueError, match='INVALID_OFFSET'):
        _list(credential_facts, PROJECT, offset=offset)


# --- search ------------------------------------------------------------------------------------------------

def test_search_matches_title_or_label_case_insensitively(credential_facts):
    _add(title='GitHub-Prod-Token')
    _add(title='jira-basic', label='Atlassian Jira')
    _add(title='unrelated')
    assert [r['title'] for r in _list(credential_facts, PROJECT, search='github')['rows']] == ['GitHub-Prod-Token']
    assert [r['title'] for r in _list(credential_facts, PROJECT, search='ATLASSIAN')['rows']] == ['jira-basic']
    result = _list(credential_facts, PROJECT, search='  jira  ')
    assert result['total'] == 1


def test_search_wildcards_are_literal(credential_facts):
    _add(title='plain-name')
    _add(title='100%_done')
    _add(title='back\\slash')
    assert [r['title'] for r in _list(credential_facts, PROJECT, search='%')['rows']] == ['100%_done']
    assert [r['title'] for r in _list(credential_facts, PROJECT, search='_')['rows']] == ['100%_done']
    assert [r['title'] for r in _list(credential_facts, PROJECT, search='\\')['rows']] == ['back\\slash']
    assert _list(credential_facts, PROJECT, search='p%e')['rows'] == []


def test_search_is_parameterized(credential_facts):
    _add(title='keep-me')
    assert _list(credential_facts, PROJECT, search="x'; DROP TABLE configuration; --")['rows'] == []
    assert _list(credential_facts, PROJECT)['total'] == 1


def test_search_total_counts_matches_only(credential_facts):
    for n in range(5):
        _add(title=f'match-{n}')
    _add(title='other')
    result = _list(credential_facts, PROJECT, search='match', limit=2)
    assert result['total'] == 5 and len(result['rows']) == 2


def test_search_limits(credential_facts):
    assert _list(credential_facts, PROJECT, search='a' * 128)['rows'] == []
    for bad in ('a' * 129, 5, ['x']):
        with pytest.raises(ValueError, match='INVALID_SEARCH'):
            _list(credential_facts, PROJECT, search=bad)


# --- the facts read is one statement (perf review 2026-10-21, item 7) ---------------------------------------

def test_facts_for_64_uuids_are_one_session_and_one_statement(credential_facts):
    governance = sys.modules[f'{PACKAGE}.methods.credential_governance']
    made = [_add(title=f'bulk-{n}') for n in range(64)]
    STATEMENTS.clear()
    SESSION_PROJECTS.clear()
    result = governance.Method.configuration_credential_facts(Self(), PROJECT, [uuid for _, uuid in made])
    assert len(result) == 64
    assert all(set(facts) == {'id', 'project_id', 'type', 'section', 'generation', 'title'} for facts in result.values())
    assert SESSION_PROJECTS == [PROJECT]
    assert len(STATEMENTS) == 1 and STATEMENTS[0].lstrip().upper().startswith('SELECT')
    assert 'configuration.data' not in STATEMENTS[0]  # metadata columns only


def test_the_list_walks_each_type_schema_once_across_calls(credential_facts, monkeypatch):
    governance = sys.modules[f'{PACKAGE}.methods.credential_governance']
    governance._PATHS_CACHE.clear()
    walks = []
    real = governance.secret_field_paths
    monkeypatch.setattr(governance, 'secret_field_paths', lambda properties: walks.append(1) or real(properties))
    _add(type_='api_cred')
    _add(type_='api_cred')
    _add(type_='plain')
    for _ in range(3):
        assert len(_list(credential_facts, PROJECT)['rows']) == 3
    assert len(walks) == 2  # api_cred and plain, once each, however many calls
