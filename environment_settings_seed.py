from __future__ import annotations

from pylon.core.tools import log

from .common_utils import get_public_project_id
from .utils import create_if_not_exists


def ensure_default_environment_settings() -> None:
    """Ensure default environment settings exist in the Public project.

    Creates the record only if it does not already exist; never overwrites.
    """

    try:
        public_project_id = get_public_project_id()
    except Exception as e:
        log.warning(f'Unable to resolve public project id: {e}')
        return

    if not public_project_id:
        log.warning('Public project id is not configured; skipping environment settings seed')
        return

    try:
        create_if_not_exists(
            {
                'project_id': public_project_id,
                'elitea_title': 'environment_settings',
                'type': 'environment_settings',
                'label': 'Environment Settings',
                'shared': True,
                'author_id': None,
                'data': {
                    'system_sender_name': 'Elitea',
                    'error_toast_duration': 20000,
                },
            }
        )
    except Exception as e:
        log.warning(f'Failed to ensure default environment settings: {e}')
