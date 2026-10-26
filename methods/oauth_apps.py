"""Admin Portal CRUD for OAuth apps (#7149); only Administration admins may write."""
from pylon.core.tools import web

from ..common_utils import get_public_project_id
from ..oauth_apps import (
    SEED_TITLE, _load_app, admin_view, check_app_connection, list_admin_apps, purge_replaced_secret,
    validated_app_data,
)
from ..exceptions import ConfigurationError
from ..local_tools import purge_secrets
from ..models.pd.oauth_app import MICROSOFT_ENTRA, OAUTH_APP_TYPE
from ..routing_access import current_actor_id, is_platform_admin
from ..utils import create_configuration, delete_configuration, update_configuration


def _require_admin():
    # A direct module method preserves the authenticated request context;
    # an unauthenticated RPC or a body-supplied actor is never sufficient.
    actor = current_actor_id()
    if not is_platform_admin(actor):
        raise PermissionError('Administration admin role is required')
    return actor


def _name(values: dict, fallback: str | None = None) -> str:
    name = str((values or {}).get('name') or fallback or '').strip()
    if not name:
        raise ConfigurationError('name', 'Name is required')
    if len(name) > 128:
        raise ConfigurationError('name', 'Name must not exceed 128 characters')
    return name


def _clear_other_defaults(project_id: int, keep_id: int, data: dict) -> None:
    """One default app per provider: the newest choice wins."""
    if not data.get('is_default'):
        return
    provider = data.get('provider') or MICROSOFT_ENTRA
    for app in list_admin_apps():
        if app['config_id'] != keep_id and app.get('is_default') and (app.get('provider') or MICROSOFT_ENTRA) == provider:
            config = _load_app(app['id'])
            update_configuration(project_id, config.id, {'data': {**config.data, 'is_default': False}})


class Method:
    @web.method()
    def oauth_apps_admin_list(self):
        _require_admin()
        return list_admin_apps()

    @web.method()
    def oauth_apps_admin_create(self, values: dict):
        actor = _require_admin()
        project_id = get_public_project_id()
        data = validated_app_data(values)
        import uuid as uuid_lib
        record = create_configuration({
            'project_id': project_id, 'author_id': actor, 'type': OAUTH_APP_TYPE, 'shared': False,
            'elitea_title': f'oauth_app_{uuid_lib.uuid4().hex[:12]}', 'label': _name(values), 'data': data,
        })
        _clear_other_defaults(project_id, record['id'], data)
        return admin_view(record)

    @web.method()
    def oauth_apps_admin_update(self, app_id: str, values: dict):
        _require_admin()
        project_id = get_public_project_id()
        config = _load_app(app_id)
        if config is None:
            raise LookupError('OAuth app not found')
        old_secret = (config.data or {}).get('client_secret')
        data = validated_app_data(values, existing=config.data)
        record = update_configuration(project_id, config.id, {
            'label': _name(values, config.label), 'data': data,
        })
        purge_replaced_secret(old_secret, (record.get('data') or {}).get('client_secret'))
        _clear_other_defaults(project_id, config.id, data)
        return admin_view(record)

    @web.method()
    def oauth_apps_admin_delete(self, app_id: str):
        _require_admin()
        project_id = get_public_project_id()
        config = _load_app(app_id)
        if config is None:
            raise LookupError('OAuth app not found')
        if config.elitea_title == SEED_TITLE:
            # Re-seeded on the next start; disabling is the lasting way to turn it off
            raise ConfigurationError('id', 'The Elitea default app cannot be deleted. Disable it instead.')
        secret = (config.data or {}).get('client_secret')
        delete_configuration(project_id, config.id)
        if secret:
            purge_secrets(project_id=project_id, secrets_to_delete=[secret])
        return True

    @web.method()
    def oauth_apps_admin_check(self, values: dict, app_id: str = None):
        """Test an app's credentials; unsaved form values win, an omitted secret uses the stored one."""
        _require_admin()
        existing = None
        if app_id:
            config = _load_app(app_id)
            if config is None:
                raise LookupError('OAuth app not found')
            existing = config.data
        data = validated_app_data(values, existing=existing)
        return check_app_connection(data)
