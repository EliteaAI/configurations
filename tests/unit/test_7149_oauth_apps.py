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
    local.log = types.SimpleNamespace(warning=lambda *a, **k: None, debug=lambda *a, **k: None)
    local.purge_secrets = lambda **kwargs: None
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
    return types.SimpleNamespace(apps=loaded['oauth_apps'], error=loaded['exceptions'].ConfigurationError)


APP = {
    'label': 'Contoso', 'client_id': 'cid', 'client_secret': '{{secret.app}}',
    'oauth_discovery_endpoint': 'https://login.microsoftonline.com/contoso',
    'scopes': {'teams': ['Chat.Read']}, 'auto_refresh_token': False,
    'enabled': True, 'allowed_project_ids': [],
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
    monkeypatch.setattr(modules.apps, '_load_app', lambda app_id: config if app_id == 'app-1' else None)


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
    modules.apps.strip_app_client_fields('outlook', data)
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
        modules.apps.validated_app_data({'client_id': 'c', 'client_secret': 's', 'oauth_discovery_endpoint': endpoint})


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
    import requests
    calls = []
    monkeypatch.setattr(requests, 'post', lambda url, **kw: calls.append((url, kw['data'])) or _Resp(body))
    result = modules.apps.check_app_connection({**APP, 'client_secret': '{{secret.app}}'})
    assert result['success'] is success and result.get('warning') is warning and text in result['message']
    url, form = calls[0]
    assert url == 'https://login.microsoftonline.com/contoso/oauth2/v2.0/token'
    assert form['client_secret'] == 'plain-secret' and form['grant_type'] == 'client_credentials'


def test_provider_defaults_to_microsoft_entra(modules):
    data = modules.apps.validated_app_data({'client_id': 'c', 'client_secret': 's'})
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
        modules.apps.validated_app_data({'client_id': 'c', 'client_secret': 's', 'credential_types': types})


def test_credential_types_keep_the_provider_order(modules):
    data = modules.apps.validated_app_data(
        {'client_id': 'c', 'client_secret': 's', 'credential_types': ['sharepoint', 'teams', 'teams']})
    assert data['credential_types'] == ['teams', 'sharepoint']
