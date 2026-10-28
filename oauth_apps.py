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
from .local_tools import db, event_manager, log, purge_secrets, VaultClient
from .models.configuration import Configuration
from .models.pd.oauth_app import (
    CREDENTIAL_PROVIDERS, MICROSOFT_ENTRA, OAUTH_APP_TOOLKIT_TYPES, OAUTH_APP_TYPE, OAUTH_PROVIDERS, OAuthApp,
    provider_of, serves, token_endpoint,
)

APP_REFERENCE_FIELD = 'oauth_app_id'
# Fields an app supplies; a Default-mode credential never stores its own copy
APP_CLIENT_FIELDS = ('client_id', 'client_secret', 'oauth_discovery_endpoint', 'scopes', 'auto_refresh_token')
SEED_TITLE = 'oauth_app_elitea_default'
# A stored login (refresh token) belongs to the OAuth client it was issued to (spec R7)
LOGIN_CLIENT_META = 'oauth_login_client_id'
ADOPTED_EVENT = 'oauth_app_default_adopted'


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
    # The project is always required: an unknown project never gets an app
    if not (app.get('allowed_project_ids') or []):
        return f"{name} has no projects assigned by your administrator"
    if project_id is None or not covers_project(app, project_id):
        return f"{name} is not available in this project"
    if is_public_app(app) and _project_has_own_apps(project_id, config_type, provider):
        # The project's own apps replace the Public tier in this project
        return f"{name} is not available in this project"
    return None


def _project_has_own_apps(project_id: int, config_type: str | None, provider) -> bool:
    """Whether ``project_id`` has a usable app of its own (not Public) for this provider and type."""
    if int(project_id) == get_public_project_id():
        return False
    for config in _public_apps():
        other = _app_view(config)
        if is_public_app(other) or provider_of(other) is not provider or other.get('enabled') is False:
            continue
        if config_type and not serves(other, config_type):
            continue
        if int(project_id) in {int(i) for i in other.get('allowed_project_ids') or []}:
            return True
    return False


def covers_project(app: dict | None, project_id: int) -> bool:
    """Whether the app applies in ``project_id``.

    The public project (``ai_project_id``) in the app's projects means **every** project, the way
    public shared credentials and default models apply everywhere; team projects mean only those.
    Projects is required, so an app without projects (never valid on save) applies nowhere.
    """
    allowed = {int(i) for i in ((app or {}).get('allowed_project_ids') or [])}
    if not allowed:
        return False
    return get_public_project_id() in allowed or int(project_id) in allowed


def is_public_app(app: dict | None) -> bool:
    """An app assigned to the public project serves every project (the Public tier)."""
    return get_public_project_id() in {int(i) for i in ((app or {}).get('allowed_project_ids') or [])}


def _active_tier(apps: list[dict], project_id: int) -> list[dict]:
    """Apps of the tier that applies in ``project_id``: a team project's own apps fully replace the
    Public tier there, even when a Public app is Default and the project's apps are not (spec 4.1).

    ``apps`` are already usable in the project (enabled, type on, covering the project).
    """
    if int(project_id) != get_public_project_id():
        own = [app for app in apps if int(project_id) in {int(i) for i in app.get('allowed_project_ids') or []}
               and not is_public_app(app)]
        if own:
            return own
    return [app for app in apps if is_public_app(app)]


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


def resolve_app_client(app_id, project_id: int | None, credential_type: str | None) -> dict:
    """Client ID, secret and token endpoint for the OAuth proxy; the secret may go to that endpoint only.

    ``app_id`` and ``credential_type`` come from the browser, so the app must serve that type in
    that project, exactly as for a saved credential.
    """
    if credential_type not in CREDENTIAL_PROVIDERS:
        raise OAuthAppUnavailable(f"Unsupported credential type: {credential_type}")
    resolved = resolve_oauth_app(app_id, project_id, toolkit_type=credential_type, unsecret=True)
    config = _load_app(app_id)
    return {
        'client_id': resolved['client_id'],
        'client_secret': resolved['client_secret'],
        'token_endpoint': token_endpoint(config.data or {}),
    }


def _is_app_only(config_type: str | None, data: dict | None) -> bool:
    """SharePoint App-only (client credentials, no user): never replaced by an admin app."""
    data = data or {}
    return (config_type == 'sharepoint' and not app_reference(data)
            and bool(data.get('client_id')) and not data.get('oauth_discovery_endpoint'))


def default_app_for(config_type: str | None, project_id: int | None) -> dict | None:
    """The Default app that applies to credentials of this type in this project, if one resolves."""
    if config_type not in OAUTH_APP_TOOLKIT_TYPES or project_id is None:
        return None
    for app in _usable_apps(project_id, config_type):
        if app.get('is_default'):
            return app
    return None


def effective_app_reference(config_type: str | None, data: dict | None, project_id: int | None) -> str | None:
    """The app a credential uses now: a resolved Default app wins over what it stores (spec R8),
    except for SharePoint App-only; otherwise its stored app, or None for its own (Custom) app."""
    if config_type not in OAUTH_APP_TOOLKIT_TYPES or _is_app_only(config_type, data):
        return app_reference(data)
    default = default_app_for(config_type, project_id)
    if default:
        return str(default['_config'].uuid)
    return app_reference(data)


def adopt_default_app(payload: dict, config_type: str | None, project_id: int | None) -> str | None:
    """Point ``payload`` at the Default app when one wins; returns its id when the stored one differs.

    The credential's own client fields are left in place (removed from the payload by
    ``apply_oauth_app``), so they stay in the database; spec section 12.
    """
    if config_type not in OAUTH_APP_TOOLKIT_TYPES:
        return None
    effective = effective_app_reference(config_type, payload, project_id)
    if not effective or effective == app_reference(payload):
        return None
    payload[APP_REFERENCE_FIELD] = effective
    return effective


def effective_client_id(config_type: str | None, data: dict | None, project_id: int | None) -> str | None:
    """Client ID the credential signs in with now: its app's, or its own."""
    app_id = effective_app_reference(config_type, data, project_id)
    if not app_id:
        return (data or {}).get('client_id')
    config = _load_app(app_id)
    return (config.data or {}).get('client_id') if config else None


def resolve_on_use(payload: dict, config, project_id: int | None) -> None:
    """Every read of a credential (agent run, sign-in, form): adopt a winning Default app.

    Read-only for the caller: a changed app is persisted by the ``oauth_app_default_adopted``
    handler, outside this request, and a login made with another client is not used.
    """
    if config.type not in OAUTH_APP_TOOLKIT_TYPES:
        return
    login_client = (getattr(config, 'meta', None) or {}).get(LOGIN_CLIENT_META)
    adopted = adopt_default_app(payload, config.type, config.project_id)
    if adopted:
        payload.pop('refresh_token', None)  # issued to the client the credential used before
        event_manager.fire_event(ADOPTED_EVENT, {
            'project_id': config.project_id, 'config_id': config.id, 'app_id': adopted,
        })
    elif payload.get('refresh_token') and login_client:
        if login_client != effective_client_id(config.type, payload, config.project_id):
            payload.pop('refresh_token', None)


def record_login_client(config_type: str | None, data: dict | None, previous: dict | None,
                        meta: dict | None, project_id: int | None) -> dict | None:
    """When a save brings a new stored login, remember the client it belongs to (R7); else None."""
    if config_type not in OAUTH_APP_TOOLKIT_TYPES or not isinstance(data, dict):
        return None
    login, before = data.get('refresh_token'), (previous or {}).get('refresh_token')
    if login == before:
        return None
    meta = dict(meta or {})
    if login:
        meta[LOGIN_CLIENT_META] = effective_client_id(config_type, data, project_id)
    else:
        meta.pop(LOGIN_CLIENT_META, None)
    return meta


def persist_adopted_app(project_id: int, config_id: int, app_id: str) -> bool:
    """Store the winning Default app on the credential (spec R8, option B); idempotent.

    Re-checks the rule against the stored row, writes with an optimistic generation check, and
    drops the stored login, which belongs to the previous client.
    """
    from .utils import update_configuration  # pylint: disable=C0415
    with db.get_session(project_id) as session:
        config = session.query(Configuration).filter_by(id=config_id).first()
        if config is None:
            return False
        data = dict(config.data or {})
        generation = config.generation
        config_type, meta = config.type, dict(config.meta or {})
    if app_reference(data) == app_id or effective_app_reference(config_type, data, project_id) != app_id:
        return False
    data[APP_REFERENCE_FIELD] = app_id
    data.pop('refresh_token', None)
    meta.pop(LOGIN_CLIENT_META, None)
    try:
        update_configuration(project_id, config_id, {'data': data, 'meta': meta}, expected_generation=generation)
    except ConfigurationError as e:
        if e.field == 'generation':
            return False  # changed meanwhile; the next use adopts again
        raise
    log.info(f'Credential {config_id} in project {project_id} now uses the default OAuth app {app_id}')
    return True


def apply_oauth_app(payload: dict, config_type: str | None, project_id: int | None, unsecret: bool) -> None:
    """Fill a Default-mode credential's client from its app, in place. Custom credentials are untouched."""
    if not uses_oauth_app(config_type, payload):
        return
    for field in APP_CLIENT_FIELDS:
        payload.pop(field, None)
    payload.update(resolve_oauth_app(app_reference(payload), project_id, config_type, unsecret))


def strip_app_client_fields(config_type: str | None, data: dict | None, project_id: int | None = None,
                            previous: dict | None = None) -> None:
    """Default mode stores the reference only, so a later admin change reaches the credential.

    The credential's own client fields from ``previous`` (stored before) are kept: they are not
    used while it points at an app, and Custom app brings them back.
    """
    if isinstance(data, dict):
        data.pop('app_error', None)  # added on read only
    if not uses_oauth_app(config_type, data):
        return
    for field in APP_CLIENT_FIELDS:
        data.pop(field, None)
    for field in APP_CLIENT_FIELDS:
        if (previous or {}).get(field) is not None:
            data[field] = previous[field]
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
        if not isinstance(data, dict):
            continue
        # A winning Default app replaces what the credential stores (R8); its own client fields
        # stay in the database but are not shown
        adopted = adopt_default_app(data, item.get('type'), item.get('project_id'))
        if adopted:
            for field in (*APP_CLIENT_FIELDS, 'refresh_token'):
                data.pop(field, None)
            if item.get('id') is not None:
                event_manager.fire_event(ADOPTED_EVENT, {
                    'project_id': item['project_id'], 'config_id': item['id'], 'app_id': adopted,
                })
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


def _usable_apps(project_id: int, toolkit_type: str | None) -> list[dict]:
    usable = []
    for config in _public_apps():
        app = _app_view(config)
        if not unavailable_reason(app, project_id, toolkit_type):
            usable.append({**app, '_config': config})
    return _active_tier(usable, project_id)


def list_available_apps(project_id: int, toolkit_type: str | None = None) -> list[dict]:
    """Apps of the active tier a project member may pick (credential type's provider), without secrets.

    Only the Default app when the tier has one (the others are cut), else only its non-default apps,
    so the UI needs no filtering: more than one row is a picker, one Default row hides the switcher.
    """
    result = []
    for app in _usable_apps(project_id, toolkit_type):
        config = app['_config']
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
    return [row for row in result if row['is_default']] or result


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
    if not (values.get('allowed_project_ids') if 'allowed_project_ids' in values else stored.get('allowed_project_ids')):
        raise ConfigurationError('allowed_project_ids', 'Select at least one project')
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
