"""Administration uses the existing environment configuration record."""
from pylon.core.tools import web
from ..common_utils import get_public_project_id
from ..utils_getters import get_project_configuration
from ..utils import create_configuration, update_configuration
from ..routing_access import current_actor_id, is_platform_admin
from ..routing_models import get_routing_models
from ..routing_readiness import classifier_options
from ..routing_settings import ALLOW_ACTIVATION, platform_policy


def platform_values(record):
    data = (record or {}).get('data') or {}
    policy, _, stored = platform_policy(data)
    return {'available': data.get('auto_routing_available') is True,
            'project_default': data.get('auto_routing_project_default') is True,
            'classifier': data.get('auto_routing_classifier'),
            # Complete platform policy; the built-in defaults while none is stored.
            'policy': policy,
            # A stored policy that no longer validates is served as the defaults; the admin UI should say so.
            'policy_stored_invalid': not stored and data.get('auto_routing_policy') is not None,
            'allow_project_activation': data.get(ALLOW_ACTIVATION) is True}


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
            flags = {'available', 'project_default'}
            optional = {'classifier', 'policy', 'allow_project_activation'}
            if not isinstance(values, dict) or not flags <= set(values) <= flags | optional or any(
                    type(values[key]) is not bool for key in flags | ({'allow_project_activation'} & set(values))):
                raise ValueError('Both platform Auto flags must be booleans')
            data = {**((record or {}).get('data') or {}), 'auto_routing_available': values['available'],
                    'auto_routing_project_default': values['project_default']}
            if 'classifier' in values:
                # Shape and visibility are validated by validate_routing_write (ConfigurationError 'classifier').
                data['auto_routing_classifier'] = values['classifier']
            if 'policy' in values:
                # Strict schema, limits and fallback visibility are enforced by validate_routing_write.
                data['auto_routing_policy'] = values['policy']
            if 'allow_project_activation' in values:
                data[ALLOW_ACTIVATION] = values['allow_project_activation']
            if record:
                record = update_configuration(project_id, record['id'], {'data': data})
            else:
                record = create_configuration({'project_id': project_id, 'author_id': actor,
                    'elitea_title': 'environment_settings', 'label': 'Environment settings',
                    'type': 'environment_settings', 'shared': True, 'data': data})
        options = classifier_options(get_routing_models(project_id, actor)['items'])
        return {**platform_values(record), 'classifier_options': options, 'can_manage': True}
