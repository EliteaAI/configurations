"""Administration uses the existing environment configuration record."""
from pylon.core.tools import web
from ..common_utils import get_public_project_id
from ..utils_getters import get_project_configuration
from ..utils import create_configuration, update_configuration
from ..routing_access import current_actor_id, is_platform_admin


def platform_values(record):
    data = (record or {}).get('data') or {}
    return {'available': data.get('auto_routing_available') is True,
            'project_default': data.get('auto_routing_project_default') is True}


class Method:
    @web.method()
    def auto_routing_platform_settings(self, values=None):
        # A direct module method preserves the authenticated request context.
        # A supplied actor ID or an unauthenticated RPC is never sufficient.
        actor = current_actor_id()
        allowed = is_platform_admin(actor)
        if not allowed:
            raise PermissionError('Administration admin role is required')
        project_id = get_public_project_id()
        record = get_project_configuration(project_id, {'type': 'environment_settings', 'elitea_title': 'environment_settings'})
        if values is not None:
            if not isinstance(values, dict) or set(values) != {'available', 'project_default'} or any(type(v) is not bool for v in values.values()):
                raise ValueError('Both platform Auto flags must be booleans')
            data = {**((record or {}).get('data') or {}), 'auto_routing_available': values['available'],
                    'auto_routing_project_default': values['project_default']}
            if record:
                record = update_configuration(project_id, record['id'], {'data': data})
            else:
                record = create_configuration({'project_id': project_id, 'author_id': actor,
                    'elitea_title': 'environment_settings', 'label': 'Environment settings',
                    'type': 'environment_settings', 'shared': True, 'data': data})
        return {**platform_values(record), 'can_manage': True}
