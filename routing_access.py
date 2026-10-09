"""Guard routing control writes at the shared API/RPC persistence boundary."""
from pydantic import ValidationError
from .common_utils import get_public_project_id
from .exceptions import ConfigurationError
from .models.pd.auto_routing import AutoRoutingProjectSettings
from .models.pd.environment_settings import EnvironmentSettings
from .tracing_access import current_actor_id, is_own_personal_project, is_project_admin

ROUTING_FIELDS = ('auto_routing_available', 'auto_routing_project_default')
CLASSIFIER_FIELD = 'auto_routing_classifier'


def _validated_data(model, data):
    try:
        return model.model_validate(data).model_dump()
    except ValidationError as exc:
        raise ConfigurationError('data', str(exc)) from exc


def _check_new_classifier(new, old, project_id, actor, *, shared_only=False):
    """A new or changed reference must be a visible, available chat model (#6826).

    An unchanged saved reference is not re-checked: a classifier that became
    unavailable must not block toggling other flags; readiness reports it.
    """
    if new is None or new == old:
        return
    from .routing_models import get_routing_models
    from .routing_readiness import find_model, usable_classifiers
    try:
        items = get_routing_models(int(project_id), actor)['items']
    except (TypeError, ValueError):
        items = []
    if find_model(usable_classifiers(items, shared_only=shared_only), new) is None:
        raise ConfigurationError('classifier', f"Model {new['name']} is not available to this project")


def is_platform_admin(actor):
    if not actor:
        return False
    try:
        from .local_tools import rpc_manager
        roles = rpc_manager.timeout(5).auth_get_user_roles(actor, 'administration') or []
        return bool({'admin', 'super_admin'} & set(roles))
    except Exception:
        return False


def validate_routing_write(config_type, project_id, payload, *, existing=None, deleting=False):
    """Return a copy with validated routing data, never trust body author_id.

    Legacy environment writes retain omitted new flags. Startup may create a
    disabled default; enabling or changing flags requires the authenticated
    Administration project admin. Project overrides require their own admin
    or personal-project owner. RPCs without request identity cannot change them.
    """
    if config_type not in {'environment_settings', 'auto_routing'}:
        return payload
    result = dict(payload)
    old = existing or {}
    old_data = old.get('data') or {}
    incoming = result.get('data')
    if config_type == 'auto_routing':
        if result.get('shared') is True:
            raise ConfigurationError('shared', 'Auto model selection settings belong to one project')
        if result.get('elitea_title', 'auto_routing') != 'auto_routing':
            raise ConfigurationError('elitea_title', 'Auto model selection settings use the ID auto_routing')
        actor = current_actor_id()
        if not actor or not (is_project_admin(project_id, actor) or is_own_personal_project(project_id, actor)):
            raise ConfigurationError('type', 'Auto model selection settings are managed by project admins')
        if incoming is not None:
            merged = dict(incoming)
            merged.setdefault('classifier', old_data.get('classifier'))
            result['data'] = _validated_data(AutoRoutingProjectSettings, merged)
            _check_new_classifier(result['data']['classifier'], old_data.get('classifier'), project_id, actor)
        return result

    if incoming is not None:
        # Older clients do not know these flags; an unrelated settings save must
        # not reset an enabled project default or the master switch.
        merged = dict(incoming)
        for key in ROUTING_FIELDS:
            merged.setdefault(key, old_data.get(key, False))
        merged.setdefault(CLASSIFIER_FIELD, old_data.get(CLASSIFIER_FIELD))
        validated = _validated_data(EnvironmentSettings, merged)
        result['data'] = validated
    else:
        validated = old_data
    if any(validated.get(key) is True for key in ROUTING_FIELDS) or validated.get(CLASSIFIER_FIELD):
        if result.get('elitea_title', old.get('elitea_title')) != 'environment_settings':
            raise ConfigurationError('elitea_title', 'Platform Auto settings require the environment_settings record')
    classifier_changed = validated.get(CLASSIFIER_FIELD) != old_data.get(CLASSIFIER_FIELD)
    changed = classifier_changed or any(validated.get(key, False) != old_data.get(key, False) for key in ROUTING_FIELDS)
    removes_enabled = (deleting or result.get('elitea_title', old.get('elitea_title')) != old.get('elitea_title')) and (
        any(old_data.get(key) is True for key in ROUTING_FIELDS) or bool(old_data.get(CLASSIFIER_FIELD)))
    if changed or removes_enabled:
        actor = current_actor_id()
        if int(project_id) != get_public_project_id() or not actor or not is_platform_admin(actor):
            raise ConfigurationError('data', 'Platform Auto model selection is managed by Administration admins')
        _check_new_classifier(validated.get(CLASSIFIER_FIELD), old_data.get(CLASSIFIER_FIELD),
                              project_id, actor, shared_only=True)
    return result
