from __future__ import annotations

from pylon.core.tools import log

from .common_utils import get_public_project_id
from .oauth_apps import SEED_TITLE
from .models.pd.oauth_app import MICROSOFT_ENTRA, OAUTH_APP_TYPE, OAUTH_PROVIDERS
from .utils import create_if_not_exists


def ensure_default_oauth_app(config: dict | None) -> None:
    """Seed the Elitea multi-tenant app (#7148) from pylon config, once.

    ``oauth_default_app`` in the plugin config holds client_id, client_secret (a vault
    reference such as {{secret.oauth_default_app_secret}} is fine) and an optional
    oauth_discovery_endpoint. Never overwrites: after seeding, admins own the record.
    """
    seed = (config or {}).get('oauth_default_app') or {}
    if not seed.get('client_id') or not seed.get('client_secret'):
        return
    try:
        public_project_id = get_public_project_id()
    except Exception as e:  # pylint: disable=W0703
        log.warning(f'Unable to resolve public project id: {e}')
        return
    try:
        create_if_not_exists({
            'project_id': public_project_id,
            'elitea_title': SEED_TITLE,
            'type': OAUTH_APP_TYPE,
            'label': seed.get('name') or 'Elitea default app',
            'shared': False,
            'author_id': None,
            'data': {
                'provider': MICROSOFT_ENTRA,
                'client_id': seed['client_id'],
                'client_secret': seed['client_secret'],
                'oauth_discovery_endpoint': seed.get('oauth_discovery_endpoint') or OAUTH_PROVIDERS[MICROSOFT_ENTRA].default_discovery_endpoint,
                'is_default': True,
                'enabled': True,
            },
        })
    except Exception as e:  # pylint: disable=W0703
        log.warning(f'Failed to ensure the default OAuth app: {e}')
