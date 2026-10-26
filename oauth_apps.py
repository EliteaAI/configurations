"""Admin-managed OAuth apps (#7149).

Apps are ``oauth_app`` configurations in the public project, never ``shared``, so the
generic configuration APIs never list them. Each app has a ``provider`` (only Microsoft
Entra ID today) and serves the credential types of that provider. A credential in Default
mode stores only ``oauth_app_id`` (the app's configuration uuid): the client ID, secret,
authority, scopes and token refresh are filled in server-side on every expansion, so an
admin change reaches every credential without a migration.

The secret lives in the public project's hidden vault. No API returns it or its vault
reference: reads expose ``client_secret_set`` only.
"""
import uuid as uuid_lib

from pydantic import ValidationError

from .common_utils import get_public_project_id
from .exceptions import ConfigurationError, handle_validation_error
from .local_tools import db, log, purge_secrets, VaultClient
from .models.configuration import Configuration
from .models.pd.oauth_app import (
    CREDENTIAL_PROVIDERS, MICROSOFT_ENTRA, OAUTH_APP_TOOLKIT_TYPES, OAUTH_APP_TYPE, OAUTH_PROVIDERS, OAuthApp,
    provider_of, serves, token_endpoint,
)

APP_REFERENCE_FIELD = 'oauth_app_id'
# Fields an app supplies; a Default-mode credential never stores its own copy
APP_CLIENT_FIELDS = ('client_id', 'client_secret', 'oauth_discovery_endpoint', 'scopes', 'auto_refresh_token')
SEED_TITLE = 'oauth_app_elitea_default'


class OAuthAppUnavailable(LookupError):
    """The referenced app is gone, disabled or not offered to this project."""


def app_reference(data: dict | None) -> str | None:
    value = (data or {}).get(APP_REFERENCE_FIELD)
    return str(value).strip() if value else None


def uses_oauth_app(config_type: str | None, data: dict | None) -> bool:
    return config_type in OAUTH_APP_TOOLKIT_TYPES and bool(app_reference(data))


def unavailable_reason(app: dict | None, project_id: int | None, config_type: str | None = None) -> str | None:
    """Why ``app`` (stored data plus ``label``) can't serve this project and credential type, or None."""
    provider_id = CREDENTIAL_PROVIDERS.get(config_type) if config_type else None
    if not app:
        label = OAUTH_PROVIDERS[provider_id].label if provider_id else 'OAuth'
        return f'The {label} app selected for this credential no longer exists'
    provider = provider_of(app)
    name = app.get('label') or f'The {provider.label} app'
    if provider_id and OAUTH_PROVIDERS[provider_id] is not provider:
        return f"{name} is a {provider.label} app and cannot be used by this credential"
    if config_type and not serves(app, config_type):
        return f"{name} is no longer offered for {config_type.capitalize()} credentials by your administrator"
    if app.get('enabled') is False:
        return f"{name} was disabled by your administrator"
    allowed = app.get('allowed_project_ids') or []
    if allowed and project_id is not None and int(project_id) not in {int(i) for i in allowed}:
        return f"{name} is not available in this project"
    return None


def _parse_uuid(value):
    try:
        return uuid_lib.UUID(str(value))
    except (TypeError, ValueError):
        return None


def _load_app(app_id) -> Configuration | None:
    uuid_value = _parse_uuid(app_id)
    if uuid_value is None:
        return None
    public_project_id = get_public_project_id()
    with db.get_session(public_project_id) as session:
        return session.query(Configuration).filter_by(
            uuid=uuid_value, type=OAUTH_APP_TYPE, project_id=public_project_id,
        ).first()


def _app_view(config: Configuration) -> dict:
    return {**(config.data or {}), 'label': config.label}


def resolve_oauth_app(app_id, project_id: int | None, toolkit_type: str | None, unsecret: bool) -> dict:
    """Client settings of a usable app; raises OAuthAppUnavailable with a user-facing message.

    Scopes are the app's list for ``toolkit_type``; when none is set they are omitted, so the
    credential model falls back to the type's recommended scopes.
    """
    config = _load_app(app_id)
    app = _app_view(config) if config else None
    reason = unavailable_reason(app, project_id, toolkit_type)
    if reason:
        raise OAuthAppUnavailable(f"{reason}. Switch to Custom app.")
    resolved = {
        'client_id': app.get('client_id'),
        'oauth_discovery_endpoint': app.get('oauth_discovery_endpoint') or provider_of(app).default_discovery_endpoint,
        'auto_refresh_token': app.get('auto_refresh_token', True),
    }
    scopes = (app.get('scopes') or {}).get(toolkit_type) if toolkit_type else None
    if scopes:
        resolved['scopes'] = list(scopes)
    if unsecret:
        resolved['client_secret'] = VaultClient(config.project_id).unsecret(app.get('client_secret'))
    return resolved


def resolve_app_client(app_id, project_id: int | None) -> dict:
    """Client ID, secret and token endpoint for the OAuth proxy; the secret may go to that endpoint only."""
    resolved = resolve_oauth_app(app_id, project_id, toolkit_type=None, unsecret=True)
    config = _load_app(app_id)
    return {
        'client_id': resolved['client_id'],
        'client_secret': resolved['client_secret'],
        'token_endpoint': token_endpoint(config.data or {}),
    }


def apply_oauth_app(payload: dict, config_type: str | None, project_id: int | None, unsecret: bool) -> None:
    """Fill a Default-mode credential's client from its app, in place. Custom credentials are untouched."""
    if not uses_oauth_app(config_type, payload):
        return
    for field in APP_CLIENT_FIELDS:
        payload.pop(field, None)
    payload.update(resolve_oauth_app(app_reference(payload), project_id, config_type, unsecret))


def strip_app_client_fields(config_type: str | None, data: dict | None, project_id: int | None = None) -> None:
    """Default mode stores the reference only, so a later admin change reaches the credential."""
    if isinstance(data, dict):
        data.pop('app_error', None)  # added on read only
    if not uses_oauth_app(config_type, data):
        return
    for field in APP_CLIENT_FIELDS:
        data.pop(field, None)
    config = _load_app(app_reference(data))
    reason = unavailable_reason(_app_view(config) if config else None, project_id, config_type)
    if reason:
        raise ConfigurationError(APP_REFERENCE_FIELD, reason)


def decorate_credentials(items: list[dict], project_id: int | None) -> None:
    """Show a Default-mode credential's non-secret settings (client ID, authority, scopes) on read.

    Token keys and login buttons are keyed by ``oauth_discovery_endpoint``, so reads carry the
    app's current value. ``app_error`` reports an app that can no longer serve the credential.
    """
    cache = {}
    for item in items:
        data = item.get('data')
        if not uses_oauth_app(item.get('type'), data):
            continue
        app_id = app_reference(data)
        if app_id not in cache:
            config = _load_app(app_id)
            cache[app_id] = _app_view(config) if config else None
        app = cache[app_id]
        reason = unavailable_reason(app, project_id, item.get('type'))
        data.pop('client_secret', None)
        if app:
            data['client_id'] = app.get('client_id')
            data['oauth_discovery_endpoint'] = app.get('oauth_discovery_endpoint')
            data['auto_refresh_token'] = app.get('auto_refresh_token', True)
            scopes = (app.get('scopes') or {}).get(item.get('type'))
            if scopes:
                data['scopes'] = list(scopes)
        if reason:
            data['app_error'] = reason


def _public_apps() -> list[Configuration]:
    public_project_id = get_public_project_id()
    with db.get_session(public_project_id) as session:
        return session.query(Configuration).filter_by(
            type=OAUTH_APP_TYPE, project_id=public_project_id,
        ).order_by(Configuration.created_at).all()


def list_available_apps(project_id: int, toolkit_type: str | None = None) -> list[dict]:
    """Apps a project member may pick (of the credential type's provider), without any secret material."""
    result = []
    for config in _public_apps():
        app = _app_view(config)
        if unavailable_reason(app, project_id, toolkit_type):
            continue
        scopes = app.get('scopes') or {}
        result.append({
            'id': str(config.uuid),
            'name': config.label,
            'provider': app.get('provider') or MICROSOFT_ENTRA,
            'client_id': app.get('client_id'),
            'oauth_discovery_endpoint': app.get('oauth_discovery_endpoint'),
            'is_default': bool(app.get('is_default')),
            'scopes': scopes.get(toolkit_type, []) if toolkit_type else scopes,
            'auto_refresh_token': app.get('auto_refresh_token', True),
        })
    return result


def credential_mode(apps: list[dict]) -> str:
    """How the Teams/Outlook/SharePoint credential form behaves for a project.

    ``custom``: no usable app, so the form is a plain Custom app form with no switcher.
    ``default``: an app is marked default, so users get it and nothing to choose.
    ``choice``: apps exist but none is the default, so users pick Default app or Custom app.
    """
    if not apps:
        return 'custom'
    return 'default' if any(app.get('is_default') for app in apps) else 'choice'


# AADSTS codes from a client-credentials request that pin down what is wrong with an app
_ENTRA_CHECK_ERRORS = {
    7000215: 'The client secret is wrong. Paste the secret Value, not its Secret ID.',
    7000222: 'The client secret has expired. Create a new one and replace it here.',
    700016: 'No app with this client ID exists in the tenant of the discovery endpoint.',
    90002: 'The tenant in the OAuth discovery endpoint does not exist.',
    900023: 'The tenant in the OAuth discovery endpoint is not valid.',
}


def check_app_connection(data: dict) -> dict:
    """Verify an app's client ID, secret and authority the way its provider allows."""
    check = _CONNECTION_CHECKS.get(data.get('provider') or MICROSOFT_ENTRA)
    if check is None:
        return {'success': False, 'message': 'Test connection is not available for this provider'}
    return check(data)


def _check_entra_connection(data: dict) -> dict:
    """Verify an Entra app's client ID, secret and authority with a client-credentials token request.

    No user signs in, so delegated scopes are not checked; Microsoft only confirms that the
    secret belongs to the app. A tenant whose policies block app-only tokens (common with
    ``/organizations``) cannot confirm the secret this way and is reported as a warning.
    """
    import requests
    secret = VaultClient(get_public_project_id()).unsecret(data.get('client_secret'))
    try:
        response = requests.post(token_endpoint(data), timeout=15, data={
            'grant_type': 'client_credentials', 'client_id': data.get('client_id'),
            'client_secret': secret, 'scope': 'https://graph.microsoft.com/.default',
        })
        body = response.json()
    except Exception as e:  # pylint: disable=W0703
        log.warning(f'Entra app check failed: {e}')
        return {'success': False, 'message': 'Microsoft sign-in could not be reached'}
    if body.get('access_token'):
        return {'success': True, 'message': 'The client ID and secret are valid'}
    codes = body.get('error_codes') or []
    for code in codes:
        if code in _ENTRA_CHECK_ERRORS:
            return {'success': False, 'message': _ENTRA_CHECK_ERRORS[code]}
    # The client was authenticated; only issuing an app-only token was refused
    if body.get('error') in ('invalid_grant', 'unauthorized_client', 'invalid_scope'):
        return {'success': True, 'warning': True, 'message': (
            'Microsoft accepted the request but would not issue an app-only token, so the secret could not '
            'be fully confirmed. Use a tenant-specific discovery endpoint, or confirm with a user sign-in. '
            f"({(body.get('error_description') or '').split(chr(10))[0][:160]})")}
    return {'success': False, 'message': (body.get('error_description') or 'Unexpected response').split('\r\n')[0][:300]}


_CONNECTION_CHECKS = {MICROSOFT_ENTRA: _check_entra_connection}


def admin_view(config: Configuration | dict) -> dict:
    """An app as the Admin Portal sees it: the secret is reported as set, never returned."""
    if isinstance(config, dict):
        data, label, uuid_value, config_id = config.get('data') or {}, config.get('label'), config.get('uuid'), config.get('id')
        created_at, updated_at = config.get('created_at'), config.get('updated_at')
    else:
        data, label, uuid_value, config_id = config.data or {}, config.label, config.uuid, config.id
        created_at, updated_at = config.created_at, config.updated_at
    view = {key: value for key, value in data.items() if key != 'client_secret'}
    view.update(
        id=str(uuid_value), config_id=config_id, name=label, provider=data.get('provider') or MICROSOFT_ENTRA,
        client_secret_set=bool(data.get('client_secret')),
        created_at=str(created_at) if created_at else None,
        updated_at=str(updated_at) if updated_at else None,
    )
    return view


def list_admin_apps() -> list[dict]:
    return [admin_view(config) for config in _public_apps()]


def validated_app_data(values: dict, existing: dict | None = None) -> dict:
    """Validate admin input; an empty or omitted secret keeps the stored one (write-only field)."""
    values = {k: v for k, v in (values or {}).items() if k in OAuthApp.model_fields}
    secret = values.get('client_secret')
    if not secret or (isinstance(secret, str) and not secret.strip()):
        if not existing or not existing.get('client_secret'):
            raise ConfigurationError('client_secret', 'Client secret is required')
        values['client_secret'] = existing['client_secret']
    stored = {k: v for k, v in (existing or {}).items() if k in OAuthApp.model_fields}
    if existing is not None and values.get('provider') and values['provider'] != (stored.get('provider') or MICROSOFT_ENTRA):
        raise ConfigurationError('provider', 'The provider of an app cannot be changed')
    try:
        app = OAuthApp.model_validate({**stored, **values})
    except ValidationError as e:
        raise handle_validation_error(e) from e
    data = app.model_dump(mode='python')
    data['client_secret'] = app.client_secret.get_secret_value()
    return data


def purge_replaced_secret(old_reference: str | None, new_reference: str | None) -> None:
    if old_reference and old_reference != new_reference:
        try:
            purge_secrets(project_id=get_public_project_id(), secrets_to_delete=[old_reference])
        except Exception as e:  # pylint: disable=W0703
            log.warning(f'Failed to purge a replaced OAuth app secret: {e}')
