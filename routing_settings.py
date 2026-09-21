"""Read current routing gates from their existing configuration owners."""
import hashlib
import json

from .common_utils import get_public_project_id
from .models.pd.auto_routing import routing_enabled
from .utils_getters import get_project_configuration


def effective_settings(environment, project):
    """Only literal booleans grant availability; malformed records fail closed.

    This revision identifies availability settings, not a published routing
    profile or authorization token. No model/credential metadata is exposed.
    """
    environment = environment if isinstance(environment, dict) else {}
    project = project if isinstance(project, dict) else {}
    env_data = environment.get('data')
    project_data = project.get('data')
    env_data = env_data if isinstance(env_data, dict) else {}
    project_data = project_data if isinstance(project_data, dict) else {}
    fields = {
        'available': env_data.get('auto_routing_available') is True,
        'project_default': env_data.get('auto_routing_project_default') is True,
        'project_override': project_data.get('enabled'),
    }
    malformed = fields['project_override'] is not None and type(fields['project_override']) is not bool
    if malformed:
        fields['project_override'] = False
    fields['enabled'] = routing_enabled(env_data, project_data)
    fields['revision'] = hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()
    return fields


def get_effective_settings(project_id):
    """Internal trusted-project read; public callers require endpoint RBAC.

    Read current flags rather than caching an enable decision across revocation.
    Callers on the fixed path need not invoke this service.
    """
    platform = get_project_configuration(get_public_project_id(), {
        'type': 'environment_settings', 'elitea_title': 'environment_settings',
    })
    project = get_project_configuration(project_id, {
        'type': 'auto_routing', 'elitea_title': 'auto_routing',
    })
    return effective_settings(platform, project)
