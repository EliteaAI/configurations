"""Admin-managed OAuth apps (#7149): availability, write-only secret, Default-mode expansion."""
import importlib.util
import pathlib
import sys
import types

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
PACKAGE = 'configurations_oauth_app_test'


class _Vault:
    def __init__(self, project_id):
        self.project_id = project_id

    def unsecret(self, value):
        return {'{{secret.app}}': 'plain-secret'}.get(value, value)


@pytest.fixture
def modules(monkeypatch):
    for suffix, path in [('', ROOT), ('.models', ROOT / 'models'), ('.models.pd', ROOT / 'models/pd')]:
        module = types.ModuleType(PACKAGE + suffix)
        module.__path__ = [str(path)]
        monkeypatch.setitem(sys.modules, module.__name__, module)
    common = types.ModuleType(PACKAGE + '.common_utils')
    common.get_public_project_id = lambda: 1
    local = types.ModuleType(PACKAGE + '.local_tools')
    local.db = None
    local.log = types.SimpleNamespace(warning=lambda *a, **k: None, debug=lambda *a, **k: None, info=lambda *a, **k: None)
    local.purge_secrets = lambda **kwargs: None
    local.event_manager = types.SimpleNamespace(fired=[])
    local.event_manager.fire_event = lambda name, payload: local.event_manager.fired.append((name, payload))
    local.VaultClient = _Vault
    config_model = types.ModuleType(PACKAGE + '.models.configuration')
    config_model.Configuration = object
    for mod in (common, local, config_model):
        monkeypatch.setitem(sys.modules, mod.__name__, mod)
    loaded = {}
    for name in ['exceptions', 'models.pd.oauth_app', 'oauth_apps']:
        spec = importlib.util.spec_from_file_location(PACKAGE + '.' + name, ROOT / (name.replace('.', '/') + '.py'))
        mod = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, spec.name, mod)
        spec.loader.exec_module(mod)
        loaded[name] = mod
    # No database in unit tests: the stored app list is set per test with _set_apps
    loaded['oauth_apps']._public_apps_uncached = loaded['oauth_apps']._public_apps
    loaded['oauth_apps'].invalidate_apps_cache()
    loaded['oauth_apps']._announced.clear()
    monkeypatch.setattr(loaded['oauth_apps'], '_public_apps', lambda: [])
    return types.SimpleNamespace(apps=loaded['oauth_apps'], error=loaded['exceptions'].ConfigurationError,
                                 events=local.event_manager)


APP = {
    'label': 'Contoso', 'client_id': 'cid', 'client_secret': '{{secret.app}}',
    'oauth_discovery_endpoint': 'https://login.microsoftonline.com/contoso',
    'scopes': {'teams': ['Chat.Read']}, 'auto_refresh_token': False,
    'enabled': True, 'allowed_project_ids': [1],
}


@pytest.mark.parametrize('app,project,expected', [
    (APP, 5, None),
    (None, 5, 'no longer exists'),
    ({**APP, 'enabled': False}, 5, 'disabled'),
    ({**APP, 'allowed_project_ids': [7]}, 5, 'not available in this project'),
    ({**APP, 'allowed_project_ids': [5]}, 5, None),
])
def test_unavailable_reason(modules, app, project, expected):
    reason = modules.apps.unavailable_reason(app, project)
    assert (reason is None) if expected is None else (expected in reason)


def _patch_app(modules, monkeypatch, data=APP):
    config = types.SimpleNamespace(data=dict(data), label=data['label'], project_id=1)
    monkeypatch.setattr(modules.apps, '_load_app', lambda app_id, fresh=False: config if app_id == 'app-1' else None)


def test_default_mode_credential_takes_everything_from_app(modules, monkeypatch):
    _patch_app(modules, monkeypatch)
    payload = {'oauth_app_id': 'app-1', 'client_id': 'stale', 'scopes': ['Mail.Send'], 'auto_refresh_token': True}
    modules.apps.apply_oauth_app(payload, 'teams', 5, unsecret=True)
    assert payload['client_id'] == 'cid'
    assert payload['client_secret'] == 'plain-secret'
    assert payload['oauth_discovery_endpoint'] == 'https://login.microsoftonline.com/contoso'
    assert payload['scopes'] == ['Chat.Read']
    assert payload['auto_refresh_token'] is False


def test_types_without_app_scopes_use_the_recommended_set(modules, monkeypatch):
    _patch_app(modules, monkeypatch)
    payload = {'oauth_app_id': 'app-1', 'scopes': ['stale']}
    modules.apps.apply_oauth_app(payload, 'outlook', 5, unsecret=False)
    assert 'scopes' not in payload  # the Outlook model fills in its recommended scopes


def test_expansion_without_unsecret_never_carries_the_secret(modules, monkeypatch):
    _patch_app(modules, monkeypatch)
    payload = {'oauth_app_id': 'app-1'}
    modules.apps.apply_oauth_app(payload, 'teams', 5, unsecret=False)
    assert 'client_secret' not in payload


def test_custom_mode_credential_is_untouched(modules):
    payload = {'client_id': 'mine', 'client_secret': 'x'}
    modules.apps.apply_oauth_app(payload, 'teams', 5, unsecret=True)
    assert payload == {'client_id': 'mine', 'client_secret': 'x'}


def test_unavailable_app_raises_user_facing_error(modules, monkeypatch):
    _patch_app(modules, monkeypatch, {**APP, 'enabled': False})
    with pytest.raises(modules.apps.OAuthAppUnavailable, match='Switch to Custom app'):
        modules.apps.apply_oauth_app({'oauth_app_id': 'app-1'}, 'teams', 5, unsecret=True)


def test_saved_default_credential_stores_reference_only(modules, monkeypatch):
    _patch_app(modules, monkeypatch)
    data = {'oauth_app_id': 'app-1', 'client_id': 'c', 'client_secret': 's', 'auto_refresh_token': True,
            'oauth_discovery_endpoint': 'https://login.microsoftonline.com/x', 'scopes': ['Mail.Read']}
    modules.apps.strip_app_client_fields('outlook', data, 5)
    assert data == {'oauth_app_id': 'app-1'}


def test_saving_with_a_missing_app_is_rejected(modules, monkeypatch):
    _patch_app(modules, monkeypatch)
    with pytest.raises(modules.error):
        modules.apps.strip_app_client_fields('teams', {'oauth_app_id': 'gone'})


def test_reads_show_client_id_and_authority_but_never_secret(modules, monkeypatch):
    _patch_app(modules, monkeypatch, {**APP, 'allowed_project_ids': [9]})
    items = [{'type': 'teams', 'data': {'oauth_app_id': 'app-1', 'client_secret': 'leak'}},
             {'type': 'teams', 'data': {'client_id': 'own', 'client_secret': '{{secret.own}}'}}]
    modules.apps.decorate_credentials(items, 5)
    assert items[0]['data']['client_id'] == 'cid'
    assert items[0]['data']['scopes'] == ['Chat.Read']
    assert items[0]['data']['auto_refresh_token'] is False
    assert 'client_secret' not in items[0]['data']
    assert 'not available in this project' in items[0]['data']['app_error']
    assert items[1]['data'] == {'client_id': 'own', 'client_secret': '{{secret.own}}'}


def test_admin_view_reports_secret_as_set_only(modules):
    view = modules.apps.admin_view({'id': 3, 'uuid': 'u', 'label': 'Contoso', 'data': APP})
    assert 'client_secret' not in view
    assert view['client_secret_set'] is True
    assert view['name'] == 'Contoso'


def test_secret_is_write_only_on_update(modules):
    existing = {**APP, 'client_secret': '{{secret.app}}'}
    data = modules.apps.validated_app_data({'name': 'x', 'client_secret': '', 'enabled': False}, existing)
    assert data['client_secret'] == '{{secret.app}}'
    assert data['enabled'] is False
    replaced = modules.apps.validated_app_data({'client_secret': 'new-value'}, existing)
    assert replaced['client_secret'] == 'new-value'


def test_new_app_requires_a_secret(modules):
    with pytest.raises(modules.error, match='Client secret is required'):
        modules.apps.validated_app_data({'client_id': 'c'})


@pytest.mark.parametrize('endpoint', ['https://evil.example/tenant', 'http://login.microsoftonline.com/x'])
def test_authority_must_be_entra(modules, endpoint):
    with pytest.raises(modules.error):
        modules.apps.validated_app_data({'client_id': 'c', 'client_secret': 's', 'allowed_project_ids': [7], 'oauth_discovery_endpoint': endpoint})


def test_records_saved_with_removed_fields_still_update(modules):
    legacy = {**APP, 'client_secret': '{{secret.app}}', 'toolkit_types': ['teams'],
              'default_for': ['teams'], 'default_scopes': {'teams': ['Chat.Read']}}
    data = modules.apps.validated_app_data({'is_default': True}, legacy)
    assert data['is_default'] is True
    assert not {'toolkit_types', 'default_for', 'default_scopes'} & set(data)


@pytest.mark.parametrize('apps,expected', [
    ([], 'custom'),
    ([{'is_default': False}], 'choice'),
    ([{'is_default': False}, {'is_default': True}], 'default'),
])
def test_credential_mode(modules, apps, expected):
    assert modules.apps.credential_mode(apps) == expected


def test_saving_outside_the_allowed_projects_is_rejected(modules, monkeypatch):
    _patch_app(modules, monkeypatch, {**APP, 'allowed_project_ids': [9]})
    with pytest.raises(modules.error, match='not available in this project'):
        modules.apps.strip_app_client_fields('teams', {'oauth_app_id': 'app-1'}, 5)


def test_read_only_app_error_is_never_stored(modules):
    data = {'client_id': 'own', 'client_secret': 's', 'app_error': 'x'}
    modules.apps.strip_app_client_fields('teams', data)
    assert 'app_error' not in data


class _Resp:
    def __init__(self, body):
        self.body = body

    def json(self):
        return self.body


@pytest.mark.parametrize('body,success,warning,text', [
    ({'access_token': 't'}, True, None, 'valid'),
    ({'error': 'invalid_client', 'error_codes': [7000215]}, False, None, 'secret Value'),
    ({'error': 'invalid_client', 'error_codes': [7000222]}, False, None, 'expired'),
    ({'error': 'unauthorized_client', 'error_codes': [700016]}, False, None, 'No app with this client ID'),
    ({'error': 'invalid_request', 'error_codes': [90002]}, False, None, 'does not exist'),
    ({'error': 'invalid_grant', 'error_codes': [53003], 'error_description': 'AADSTS53003: blocked'}, True, True,
     'could not be fully confirmed'),
])
def test_admin_connection_check(modules, monkeypatch, body, success, warning, text):
    # A stub, so the test needs no HTTP library (CI installs only tests/requirements-dev.txt)
    calls = []
    requests = types.ModuleType('requests')
    requests.RequestException = Exception
    requests.post = lambda url, **kw: calls.append((url, kw['data'])) or _Resp(body)
    monkeypatch.setitem(sys.modules, 'requests', requests)
    result = modules.apps.check_app_connection({**APP, 'client_secret': '{{secret.app}}'})
    assert result['success'] is success and result.get('warning') is warning and text in result['message']
    url, form = calls[0]
    assert url == 'https://login.microsoftonline.com/contoso/oauth2/v2.0/token'
    assert form['client_secret'] == 'plain-secret' and form['grant_type'] == 'client_credentials'


def test_provider_defaults_to_microsoft_entra(modules):
    data = modules.apps.validated_app_data({'client_id': 'c', 'client_secret': 's', 'allowed_project_ids': [7]})
    assert data['provider'] == 'microsoft_entra'
    assert data['oauth_discovery_endpoint'] == 'https://login.microsoftonline.com/organizations'


def test_unknown_provider_is_rejected(modules):
    with pytest.raises(modules.error):
        modules.apps.validated_app_data({'provider': 'google', 'client_id': 'c', 'client_secret': 's'})


def test_provider_cannot_change_after_creation(modules):
    with pytest.raises(modules.error, match='cannot be changed'):
        modules.apps.validated_app_data({'provider': 'other'}, {**APP, 'provider': 'microsoft_entra'})


def test_scopes_only_for_the_providers_credential_types(modules):
    with pytest.raises(modules.error):
        modules.apps.validated_app_data({**APP, 'client_secret': 's', 'scopes': {'gmail': ['x']}})


def test_records_without_provider_are_entra_apps(modules):
    assert modules.apps.unavailable_reason(APP, 5, 'teams') is None
    assert modules.apps.admin_view({'data': APP, 'label': 'Contoso', 'uuid': 'u', 'id': 1})['provider'] == 'microsoft_entra'


def test_app_of_another_provider_cannot_serve_the_credential(modules, monkeypatch):
    other = modules.apps.OAUTH_PROVIDERS['microsoft_entra'].__class__(
        label='Other', credential_types=('other_type',), hosts=frozenset({'login.other.test'}),
        default_discovery_endpoint='https://login.other.test', discovery_endpoint_hint='', token_path='/token')
    monkeypatch.setitem(modules.apps.OAUTH_PROVIDERS, 'other', other)
    monkeypatch.setitem(modules.apps.CREDENTIAL_PROVIDERS, 'other_type', 'other')
    assert 'cannot be used by this credential' in modules.apps.unavailable_reason(APP, 5, 'other_type')
    assert 'cannot be used by this credential' in modules.apps.unavailable_reason({**APP, 'provider': 'other'}, 5, 'teams')


def test_token_endpoint_comes_from_the_provider(modules):
    assert modules.apps.token_endpoint(APP) == 'https://login.microsoftonline.com/contoso/oauth2/v2.0/token'


def test_a_credential_type_switched_off_hides_the_app(modules, monkeypatch):
    app = {**APP, 'credential_types': ['teams', 'sharepoint']}
    assert modules.apps.unavailable_reason(app, 5, 'teams') is None
    reason = modules.apps.unavailable_reason(app, 5, 'outlook')
    assert 'no longer offered for Outlook credentials' in reason


def test_unset_credential_types_serve_every_type_of_the_provider(modules):
    for config_type in ('teams', 'outlook', 'sharepoint'):
        assert modules.apps.unavailable_reason(APP, 5, config_type) is None


@pytest.mark.parametrize('types,error', [([], 'at least one'), (['jira'], 'cannot serve')])
def test_credential_types_are_validated(modules, types, error):
    with pytest.raises(modules.error, match=error):
        modules.apps.validated_app_data({'client_id': 'c', 'client_secret': 's', 'allowed_project_ids': [7], 'credential_types': types})


def test_credential_types_keep_the_provider_order(modules):
    data = modules.apps.validated_app_data(
        {'client_id': 'c', 'client_secret': 's', 'allowed_project_ids': [7],
         'credential_types': ['sharepoint', 'teams', 'teams']})
    assert data['credential_types'] == ['teams', 'sharepoint']


@pytest.mark.parametrize('allowed,project,expected', [
    ([1], 5, None),                 # the public project (stub id 1) = every project
    ([1], 1, None),
    ([7], 5, 'not available'),      # a team project only
    ([7], 7, None),
    ([], 5, 'not available in this project'),  # Projects is required: an app without projects applies nowhere
])
def test_public_project_means_every_project(modules, allowed, project, expected):
    reason = modules.apps.unavailable_reason({**APP, 'allowed_project_ids': allowed}, project, 'teams')
    assert (reason is None) if expected is None else (expected in reason)


def test_projects_are_required_on_save(modules):
    with pytest.raises(modules.error, match='Select at least one project'):
        modules.apps.validated_app_data({'client_id': 'c', 'client_secret': 's'})
    with pytest.raises(modules.error):
        modules.apps.validated_app_data({'client_id': 'c', 'client_secret': 's', 'allowed_project_ids': []})


def test_an_update_cannot_drop_all_projects(modules):
    existing = {**APP, 'client_secret': '{{secret.app}}'}
    with pytest.raises(modules.error):
        modules.apps.validated_app_data({'allowed_project_ids': []}, existing)


def test_no_project_never_resolves_an_app(modules):
    assert 'not available in this project' in modules.apps.unavailable_reason(APP, None, 'teams')


def _set_apps(modules, monkeypatch, *apps):
    configs = [types.SimpleNamespace(data=dict(a), label=a['label'], project_id=1,
                                     uuid=f"uuid-{a['label']}", created_at=None) for a in apps]
    monkeypatch.setattr(modules.apps, '_public_apps', lambda: configs)


PUBLIC_DEFAULT = {**APP, 'label': 'Public default', 'allowed_project_ids': [1], 'is_default': True,
                  'credential_types': ['outlook', 'sharepoint']}
TEAM_APP = {**APP, 'label': 'Team app', 'allowed_project_ids': [3], 'is_default': False,
            'credential_types': ['teams', 'outlook', 'sharepoint']}


def _names(modules, project_id, config_type):
    rows = modules.apps.list_available_apps(project_id, config_type)
    return [r['name'] for r in rows], modules.apps.credential_mode(rows)


def test_team_project_apps_replace_public_apps(modules, monkeypatch):
    """Public Default (Outlook/SharePoint) + a non-default team app: the team project sees only its app."""
    _set_apps(modules, monkeypatch, PUBLIC_DEFAULT, TEAM_APP)
    assert _names(modules, 3, 'outlook') == (['Team app'], 'choice')
    assert _names(modules, 3, 'teams') == (['Team app'], 'choice')


def test_other_projects_keep_the_public_tier(modules, monkeypatch):
    _set_apps(modules, monkeypatch, PUBLIC_DEFAULT, TEAM_APP)
    assert _names(modules, 5, 'outlook') == (['Public default'], 'default')
    assert _names(modules, 5, 'teams') == ([], 'custom')


def test_project_tier_counts_only_types_switched_on(modules, monkeypatch):
    """A team app without Outlook does not hide the Public Outlook app in that project."""
    _set_apps(modules, monkeypatch, PUBLIC_DEFAULT, {**TEAM_APP, 'credential_types': ['teams']})
    assert _names(modules, 3, 'outlook') == (['Public default'], 'default')


def test_a_public_app_is_unavailable_where_the_project_has_its_own(modules, monkeypatch):
    _set_apps(modules, monkeypatch, PUBLIC_DEFAULT, TEAM_APP)
    assert 'not available in this project' in modules.apps.unavailable_reason(PUBLIC_DEFAULT, 3, 'outlook')
    assert modules.apps.unavailable_reason(PUBLIC_DEFAULT, 5, 'outlook') is None


def test_a_default_app_cuts_the_tier_non_default_apps(modules, monkeypatch):
    other = {**PUBLIC_DEFAULT, 'label': 'Public other', 'is_default': False}
    _set_apps(modules, monkeypatch, other, PUBLIC_DEFAULT)
    assert _names(modules, 5, 'outlook') == (['Public default'], 'default')


def test_without_a_default_every_non_default_app_is_listed(modules, monkeypatch):
    second = {**TEAM_APP, 'label': 'Team app 2'}
    _set_apps(modules, monkeypatch, TEAM_APP, second)
    assert _names(modules, 3, 'teams') == (['Team app', 'Team app 2'], 'choice')


@pytest.mark.parametrize('credential_type', ['teams', None, 'jira'])
def test_proxy_client_requires_a_type_the_app_serves(modules, monkeypatch, credential_type):
    _set_apps(modules, monkeypatch, PUBLIC_DEFAULT)
    monkeypatch.setattr(modules.apps, '_load_app', lambda app_id, fresh=False: modules.apps._public_apps()[0])
    with pytest.raises(modules.apps.OAuthAppUnavailable):
        modules.apps.resolve_app_client('uuid-Public default', 5, credential_type)


# --- R8, option B: a resolved Default app wins and is stored ------------------------------------

OWN = {'client_id': 'own', 'client_secret': '{{secret.own}}',
       'oauth_discovery_endpoint': 'https://login.microsoftonline.com/own', 'scopes': ['Mail.Read']}


def _credential(data, type_='outlook', project_id=5, meta=None):
    return types.SimpleNamespace(id=9, type=type_, project_id=project_id, data=dict(data), meta=meta or {},
                                 generation=3)


def test_default_app_wins_over_a_custom_credential(modules, monkeypatch):
    _set_apps(modules, monkeypatch, PUBLIC_DEFAULT)
    payload = {**OWN, 'refresh_token': 'own-login'}
    modules.apps.resolve_on_use(payload, _credential(OWN), 5)
    assert payload['oauth_app_id'] == 'uuid-Public default'
    assert 'refresh_token' not in payload  # issued to the user's own app
    assert modules.events.fired == [('oauth_app_default_adopted',
                                     {'project_id': 5, 'config_id': 9, 'app_id': 'uuid-Public default'})]


def test_default_app_wins_over_a_picked_non_default_app(modules, monkeypatch):
    other = {**PUBLIC_DEFAULT, 'label': 'Other', 'is_default': False}
    _set_apps(modules, monkeypatch, other, PUBLIC_DEFAULT)
    payload = {'oauth_app_id': 'uuid-Other'}
    modules.apps.resolve_on_use(payload, _credential(payload), 5)
    assert payload['oauth_app_id'] == 'uuid-Public default'


def test_without_a_default_the_credential_keeps_what_it_stores(modules, monkeypatch):
    _set_apps(modules, monkeypatch, {**PUBLIC_DEFAULT, 'is_default': False})
    payload = dict(OWN)
    modules.apps.resolve_on_use(payload, _credential(OWN), 5)
    assert 'oauth_app_id' not in payload and modules.events.fired == []


def test_sharepoint_app_only_is_never_replaced(modules, monkeypatch):
    _set_apps(modules, monkeypatch, PUBLIC_DEFAULT)
    app_only = {'client_id': 'own', 'client_secret': '{{secret.own}}', 'site_url': 'https://x.sharepoint.com'}
    payload = dict(app_only)
    modules.apps.resolve_on_use(payload, _credential(app_only, 'sharepoint'), 5)
    assert 'oauth_app_id' not in payload and modules.events.fired == []


def test_the_credential_own_project_decides(modules, monkeypatch):
    """Team project 3 has its own (non-default) app, so the Public Default doesn't win there."""
    _set_apps(modules, monkeypatch, PUBLIC_DEFAULT, TEAM_APP)
    payload = dict(OWN)
    modules.apps.resolve_on_use(payload, _credential(OWN, project_id=3), 3)
    assert 'oauth_app_id' not in payload


def test_a_login_made_with_another_client_is_not_used(modules, monkeypatch):
    _set_apps(modules, monkeypatch, {**PUBLIC_DEFAULT, 'is_default': False})
    payload = {**OWN, 'refresh_token': 'login'}
    modules.apps.resolve_on_use(payload, _credential(OWN, meta={'oauth_login_client_id': 'cid'}), 5)
    assert 'refresh_token' not in payload


def test_a_login_of_the_same_client_is_kept(modules, monkeypatch):
    _set_apps(modules, monkeypatch)
    payload = {**OWN, 'refresh_token': 'login'}
    modules.apps.resolve_on_use(payload, _credential(OWN, meta={'oauth_login_client_id': 'own'}), 5)
    assert payload['refresh_token'] == 'login'


def test_saving_a_default_app_credential_keeps_the_own_fields(modules, monkeypatch):
    _patch_app(modules, monkeypatch, {**APP, 'allowed_project_ids': [1]})
    data = {'oauth_app_id': 'app-1', 'client_id': 'from-form'}
    modules.apps.strip_app_client_fields('teams', data, 5, previous=OWN)
    assert data == {'oauth_app_id': 'app-1', **OWN}


def test_a_new_login_records_its_client(modules, monkeypatch):
    _set_apps(modules, monkeypatch)
    meta = modules.apps.record_login_client('outlook', {**OWN, 'refresh_token': 'new'}, OWN, {}, 5)
    assert meta == {'oauth_login_client_id': 'own'}
    assert modules.apps.record_login_client('outlook', dict(OWN), dict(OWN), {}, 5) is None


def test_the_read_view_hides_own_fields_when_a_default_wins(modules, monkeypatch):
    _set_apps(modules, monkeypatch, PUBLIC_DEFAULT)
    monkeypatch.setattr(modules.apps, '_load_app', lambda app_id, fresh=False: modules.apps._public_apps()[0])
    item = {'id': 9, 'project_id': 5, 'type': 'outlook', 'data': {**OWN, 'refresh_token': 'x'}}
    modules.apps.decorate_credentials([item], 5)
    assert item['data']['oauth_app_id'] == 'uuid-Public default'
    assert item['data']['client_id'] == 'cid' and 'client_secret' not in item['data']
    assert 'refresh_token' not in item['data']


class _Stored:
    def __init__(self, data, meta=None):
        self.row = types.SimpleNamespace(id=9, type='outlook', data=dict(data), meta=meta or {}, generation=3)
        self.updates = []


def _persist(modules, monkeypatch, stored, conflict=False):
    class _Session:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def query(self, _):
            return types.SimpleNamespace(filter_by=lambda **kw: types.SimpleNamespace(first=lambda: stored.row))

    monkeypatch.setattr(modules.apps, 'db', types.SimpleNamespace(get_session=lambda pid: _Session()))
    utils = types.ModuleType(PACKAGE + '.utils')

    def update_configuration(project_id, config_id, payload, expected_generation=None):
        if conflict:
            raise modules.error('generation', 'Configuration was modified concurrently')
        stored.updates.append((payload, expected_generation))
    utils.update_configuration = update_configuration
    monkeypatch.setitem(sys.modules, utils.__name__, utils)
    return modules.apps.persist_adopted_app(5, 9, 'uuid-Public default')


def test_persist_stores_the_default_app_and_drops_the_old_login(modules, monkeypatch):
    _set_apps(modules, monkeypatch, PUBLIC_DEFAULT)
    stored = _Stored({**OWN, 'refresh_token': 'x'}, {'oauth_login_client_id': 'own'})
    assert _persist(modules, monkeypatch, stored) is True
    (payload, generation), = stored.updates
    assert generation == 3
    assert payload['data'] == {**OWN, 'oauth_app_id': 'uuid-Public default'}  # own fields kept
    assert payload['meta'] == {}


def test_persist_is_idempotent_and_rechecks_the_rule(modules, monkeypatch):
    _set_apps(modules, monkeypatch, PUBLIC_DEFAULT)
    stored = _Stored({**OWN, 'oauth_app_id': 'uuid-Public default'})
    assert _persist(modules, monkeypatch, stored) is False and stored.updates == []
    _set_apps(modules, monkeypatch, {**PUBLIC_DEFAULT, 'is_default': False})  # admin un-defaulted meanwhile
    stored = _Stored(OWN)
    assert _persist(modules, monkeypatch, stored) is False and stored.updates == []


def test_persist_gives_up_on_a_concurrent_edit(modules, monkeypatch):
    _set_apps(modules, monkeypatch, PUBLIC_DEFAULT)
    assert _persist(modules, monkeypatch, _Stored(OWN), conflict=True) is False


# --- PR review: scopes on sign-in, shared, cached app list -------------------------------------

def test_sign_in_uses_the_admin_scopes(modules, monkeypatch):
    _set_apps(modules, monkeypatch, {**APP, 'label': 'Teams app', 'credential_types': ['teams']})
    monkeypatch.setattr(modules.apps, '_load_app', lambda app_id, fresh=False: modules.apps._public_apps()[0])
    client = modules.apps.resolve_app_client('uuid-Teams app', 5, 'teams')
    assert client['scopes'] == ['Chat.Read']
    assert client['client_secret'] == 'plain-secret'


def test_sign_in_falls_back_to_the_type_recommended_scopes(modules, monkeypatch):
    _set_apps(modules, monkeypatch, {**APP, 'label': 'Teams app', 'scopes': {}})
    monkeypatch.setattr(modules.apps, '_load_app', lambda app_id, fresh=False: modules.apps._public_apps()[0])
    monkeypatch.setattr(modules.apps, 'recommended_scopes', lambda type_: ['User.ReadBasic.All'])
    assert modules.apps.resolve_app_client('uuid-Teams app', 5, 'teams')['scopes'] == ['User.ReadBasic.All']


@pytest.mark.parametrize('config_type,shared,rejected', [
    ('teams', True, True), ('outlook', True, True), ('sharepoint', True, True),
    ('teams', False, False), ('jira', True, False),
])
def test_teams_outlook_sharepoint_credentials_cannot_be_shared(modules, config_type, shared, rejected):
    if rejected:
        with pytest.raises(modules.error, match='cannot be shared'):
            modules.apps.reject_shared(config_type, shared)
    else:
        modules.apps.reject_shared(config_type, shared)


def test_the_app_list_is_read_once_and_cleared_by_admin_writes(modules, monkeypatch):
    queries = []

    class _Session:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def query(self, _):
            queries.append(1)
            chain = types.SimpleNamespace(all=lambda: [])
            chain.order_by = lambda *_: chain
            return types.SimpleNamespace(filter_by=lambda **kw: chain)

    loaded = sys.modules[PACKAGE + '.oauth_apps']
    monkeypatch.setattr(loaded, 'db', types.SimpleNamespace(get_session=lambda pid: _Session()))
    monkeypatch.setattr(loaded, 'Configuration', types.SimpleNamespace(created_at=None))
    monkeypatch.setattr(loaded, '_public_apps', loaded._public_apps_uncached)
    loaded.invalidate_apps_cache()
    for _ in range(3):
        loaded._public_apps()
    assert len(queries) == 1
    loaded.invalidate_apps_cache()
    loaded._public_apps()
    assert len(queries) == 2


def test_the_switch_is_announced_once_per_credential(modules, monkeypatch):
    _set_apps(modules, monkeypatch, PUBLIC_DEFAULT)
    for _ in range(3):
        modules.apps.resolve_on_use(dict(OWN), _credential(OWN), 5)
    assert len(modules.events.fired) == 1


def test_outside_its_projects_the_app_is_never_named(modules):
    for app, project in [({**APP, 'allowed_project_ids': [7]}, 5), ({**APP, 'allowed_project_ids': []}, 5), (APP, None)]:
        reason = modules.apps.unavailable_reason(app, project, 'teams')
        assert 'not available in this project' in reason and 'Contoso' not in reason
    assert 'Contoso' in modules.apps.unavailable_reason({**APP, 'enabled': False}, 5, 'teams')  # in scope: named


# ms_graph: a 4th Entra credential type, opt-in per app, with free-text Graph scopes

def test_unset_credential_types_do_not_serve_opt_in_ms_graph(modules):
    assert modules.apps.unavailable_reason(APP, 5, 'ms_graph') is not None
    assert modules.apps.unavailable_reason({**APP, 'credential_types': ['ms_graph']}, 5, 'ms_graph') is None
    assert not modules.apps.serves(APP, 'ms_graph')
    assert modules.apps.serves({**APP, 'credential_types': ['ms_graph']}, 'ms_graph')


def test_admin_view_shows_the_effective_credential_types(modules):
    view = modules.apps.admin_view({'data': APP, 'label': 'Contoso', 'uuid': 'u', 'id': 1})
    assert view['credential_types'] == ['teams', 'outlook', 'sharepoint']
    view = modules.apps.admin_view({'data': {**APP, 'credential_types': ['ms_graph']}, 'label': 'C', 'uuid': 'u', 'id': 1})
    assert view['credential_types'] == ['ms_graph']


def test_ms_graph_scopes_are_free_text_graph_permissions(modules):
    data = modules.apps.validated_app_data(
        {'client_id': 'c', 'client_secret': 's', 'allowed_project_ids': [7], 'credential_types': ['ms_graph'],
         'scopes': {'ms_graph': ['https://graph.microsoft.com/Mail.Read', 'Files.ReadWrite.All', 'mail.read',
                                 'offline_access']}})
    assert data['scopes'] == {'ms_graph': ['Mail.Read', 'Files.ReadWrite.All']}


@pytest.mark.parametrize('scopes', [['https://contoso.sharepoint.com/AllSites.Read'], ['Mail'],
                                    ['https://graph.microsoft.com/.default', 'Mail.Read']])
def test_ms_graph_scopes_reject_other_resources(modules, scopes):
    with pytest.raises(modules.error):
        modules.apps.validated_app_data(
            {'client_id': 'c', 'client_secret': 's', 'allowed_project_ids': [7], 'credential_types': ['ms_graph'],
             'scopes': {'ms_graph': scopes}})
