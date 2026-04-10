from __future__ import annotations

from pylon.core.tools import log

from .common_utils import get_public_project_id
from .utils import create_if_not_exists
from .models.pd.service_prompt_defaults import SERVICE_PROMPT_DEFAULTS


def ensure_default_service_prompts() -> None:
    """Ensure default service prompts exist in the Public project.

    Creates missing records only; never overwrites existing prompts.
    """

    try:
        public_project_id = get_public_project_id()
    except Exception as e:
        log.warning(f'Unable to resolve public project id: {e}')
        return

    if not public_project_id:
        log.warning('Public project id is not configured; skipping default service prompts seed')
        return

    for key, prompt in SERVICE_PROMPT_DEFAULTS.items():
        if not prompt:
            continue
        try:
            create_if_not_exists(
                {
                    'project_id': public_project_id,
                    'elitea_title': key,
                    'type': 'service_prompt',
                    'label': key.replace('_', ' ').title(),
                    'shared': True,
                    'author_id': None,
                    'data': {
                        'key': key,
                        'prompt': prompt,
                    },
                }
            )
        except Exception as e:
            log.warning(f'Failed to ensure default service prompt {key}: {e}')
