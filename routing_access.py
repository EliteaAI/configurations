"""Guard routing control writes at the shared API/RPC persistence boundary."""
from pydantic import ValidationError
from .common_utils import get_public_project_id
from .exceptions import ConfigurationError
from .models.pd.auto_routing import AutoRoutingProjectSettings, budgets_increasing
from .models.pd.environment_settings import EnvironmentSettings
from .tracing_access import current_actor_id, is_own_personal_project, is_project_admin

ROUTING_FIELDS = ('auto_routing_available', 'auto_routing_project_default')
CLASSIFIER_FIELD = 'auto_routing_classifier'
POLICY_FIELD = 'auto_routing_policy'
POLICY_ENABLED_FIELD = 'auto_routing_policy_enabled'
MISSING = object()
ALLOW_ACTIVATION_FIELD = 'auto_routing_policy_allow_project_activation'


def _validated_data(model, data):
    try:
        parsed = model.model_validate(data)
    except ValidationError as exc:
        raise ConfigurationError('data', str(exc)) from exc
    result = parsed.model_dump()
    if getattr(parsed, 'policy', None) is not None:
        # A project override keeps only the keys that were set, so "inherit" and an explicit null stay distinct.
        result['policy'] = parsed.policy.model_dump(exclude_unset=True)
    else:
        result.pop('policy', None)
    return result


def _actor_items(project_id, actor):
    from .routing_models import get_routing_models
    try:
        return get_routing_models(int(project_id), actor)['items']
    except (TypeError, ValueError):
        return []


def _check_new_classifier(new, old, project_id, actor, *, shared_only=False, field='classifier'):
    """A new or changed reference must be a visible, available chat model (#6826).

    An unchanged saved reference is not re-checked: a classifier that became
    unavailable must not block toggling other flags; readiness reports it.
    """
    if new is None or new == old:
        return
    from .routing_readiness import find_model, usable_classifiers
    items = _actor_items(project_id, actor)
    if find_model(usable_classifiers(items, shared_only=shared_only), new) is None:
        raise ConfigurationError(field, f"Model {new['name']} is not available to this project")


def _check_pool_entries(new, old, project_id, actor, *, shared_only=False):
    """A new or changed pool lists only visible deployments: canonical ids or exact names (#6722)."""
    if not new or new == old:
        return
    known = set()
    for model in _actor_items(project_id, actor):
        if shared_only and model.get('shared') is not True:
            continue
        known.add(model.get('name'))
        known.add((model.get('identity') or {}).get('canonical'))
    unknown = list(dict.fromkeys(entry for entry in new if entry not in known))
    if unknown:
        listed = ', '.join(unknown[:5]) + (f' and {len(unknown) - 5} more' if len(unknown) > 5 else '')
        raise ConfigurationError('policy.pool', f'Unknown model(s): {listed}')


def _check_project_policy(new, old, project_id, actor):
    """A changed project policy stays inside platform limits; a changed fallback must be usable (#6722).

    Unchanged values are not re-checked: a platform that later lowers a limit is enforced at merge time
    (routing_settings.effective_policy), and must not block unrelated saves.
    """
    new, old = new or {}, old or {}
    if new == old:
        return
    from .routing_settings import _data, platform_policy
    from .utils_getters import get_project_configuration
    platform, allow_activation, _ = platform_policy(_data(get_project_configuration(
        get_public_project_id(), {'type': 'environment_settings', 'elitea_title': 'environment_settings'})))
    output, old_output = new.get('output') or {}, old.get('output') or {}
    for key in ('min_cap', 'max_cap', 'default_allowance'):
        if output.get(key) is not None and output[key] != old_output.get(key) and output[key] > platform['output']['max_cap']:
            raise ConfigurationError(f'policy.output.{key}',
                                     f"Project {key} cannot exceed the platform maximum of {platform['output']['max_cap']}")
    budgets = new.get('thinking_budget_tokens') or {}
    if budgets and budgets != (old.get('thinking_budget_tokens') or {}) and not budgets_increasing(
            {**platform['thinking_budget_tokens'], **budgets}):
        raise ConfigurationError('policy.thinking_budget_tokens',
                                 'Thinking budgets must satisfy low < medium < high after merging with the platform policy')
    if (new.get('shadow_mode') is False and old.get('shadow_mode') is not False
            and platform['shadow_mode'] is True and not allow_activation):
        raise ConfigurationError('policy.shadow_mode', 'The platform policy does not allow projects to leave shadow mode')
    # A project may narrow the platform pool and choose its own fallback, never lift a platform restriction.
    if 'pool' in new and new['pool'] != old.get('pool', MISSING) and platform['pool'] is not None:
        if new['pool'] is None:
            raise ConfigurationError('policy.pool', 'Projects cannot lift the platform pool restriction')
        outside = [entry for entry in new['pool'] if entry not in platform['pool']]
        if outside:
            raise ConfigurationError('policy.pool', 'The project pool must be a subset of the platform pool: ' + ', '.join(outside[:5]))
    if ('fallback' in new and new['fallback'] is None and old.get('fallback', MISSING) is not None
            and platform['fallback'] is not None):
        raise ConfigurationError('policy.fallback', 'Projects cannot remove the platform fallback')
    _check_pool_entries(new.get('pool'), old.get('pool'), project_id, actor)
    selector, old_selector = new.get('selector') or {}, old.get('selector') or {}
    artifact_changed = 'artifact' in selector and ('artifact' not in old_selector or selector['artifact'] != old_selector['artifact'])
    if (new.get('shadow_mode') is not None and new['shadow_mode'] != old.get('shadow_mode')) or artifact_changed:
        shadow = platform['shadow_mode'] if new.get('shadow_mode') is None else new['shadow_mode']
        artifact = selector['artifact'] if 'artifact' in selector else platform['selector']['artifact']
        _require_artifact(shadow, artifact)
    _check_new_classifier(new.get('fallback'), old.get('fallback'), project_id, actor, field='policy.fallback')


def _require_artifact(shadow_mode, artifact):
    if shadow_mode is False and artifact is None:
        raise ConfigurationError('policy.selector.artifact', 'Leaving shadow mode requires a pinned selector artifact')


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
            merged.setdefault('policy', old_data.get('policy'))
            result['data'] = _validated_data(AutoRoutingProjectSettings, merged)
            _check_new_classifier(result['data']['classifier'], old_data.get('classifier'), project_id, actor)
            _check_project_policy(result['data'].get('policy'), old_data.get('policy'), project_id, actor)
        return result

    if incoming is not None:
        # Older clients do not know these flags; an unrelated settings save must
        # not reset an enabled project default or the master switch.
        merged = dict(incoming)
        for key in ROUTING_FIELDS:
            merged.setdefault(key, old_data.get(key, False))
        merged.setdefault(CLASSIFIER_FIELD, old_data.get(CLASSIFIER_FIELD))
        merged.setdefault(POLICY_FIELD, old_data.get(POLICY_FIELD))
        merged.setdefault(ALLOW_ACTIVATION_FIELD, old_data.get(ALLOW_ACTIVATION_FIELD, False))
        merged.setdefault(POLICY_ENABLED_FIELD, old_data.get(POLICY_ENABLED_FIELD, False))
        validated = _validated_data(EnvironmentSettings, merged)
        result['data'] = validated
    else:
        validated = old_data
    if (any(validated.get(key) is True for key in ROUTING_FIELDS) or validated.get(CLASSIFIER_FIELD)
            or validated.get(POLICY_FIELD) or validated.get(ALLOW_ACTIVATION_FIELD) is True
            or validated.get(POLICY_ENABLED_FIELD) is True):
        if result.get('elitea_title', old.get('elitea_title')) != 'environment_settings':
            raise ConfigurationError('elitea_title', 'Platform Auto settings require the environment_settings record')
    classifier_changed = validated.get(CLASSIFIER_FIELD) != old_data.get(CLASSIFIER_FIELD)
    policy_changed = (validated.get(POLICY_FIELD) != old_data.get(POLICY_FIELD)
                      or validated.get(ALLOW_ACTIVATION_FIELD, False) != old_data.get(ALLOW_ACTIVATION_FIELD, False)
                      or validated.get(POLICY_ENABLED_FIELD, False) != old_data.get(POLICY_ENABLED_FIELD, False))
    changed = classifier_changed or policy_changed or any(validated.get(key, False) != old_data.get(key, False) for key in ROUTING_FIELDS)
    removes_enabled = (deleting or result.get('elitea_title', old.get('elitea_title')) != old.get('elitea_title')) and (
        any(old_data.get(key) is True for key in ROUTING_FIELDS) or bool(old_data.get(CLASSIFIER_FIELD))
        or bool(old_data.get(POLICY_FIELD)) or old_data.get(ALLOW_ACTIVATION_FIELD) is True
        or old_data.get(POLICY_ENABLED_FIELD) is True)
    if changed or removes_enabled:
        actor = current_actor_id()
        if int(project_id) != get_public_project_id() or not actor or not is_platform_admin(actor):
            raise ConfigurationError('data', 'Platform Auto model selection is managed by Administration admins')
        _check_new_classifier(validated.get(CLASSIFIER_FIELD), old_data.get(CLASSIFIER_FIELD),
                              project_id, actor, shared_only=True)
        if policy_changed:
            policy = validated.get(POLICY_FIELD) or {}
            _require_artifact(policy.get('shadow_mode'), (policy.get('selector') or {}).get('artifact'))
        _check_pool_entries((validated.get(POLICY_FIELD) or {}).get('pool'),
                            (old_data.get(POLICY_FIELD) or {}).get('pool'), project_id, actor, shared_only=True)
        _check_new_classifier((validated.get(POLICY_FIELD) or {}).get('fallback'),
                              (old_data.get(POLICY_FIELD) or {}).get('fallback'),
                              project_id, actor, shared_only=True, field='policy.fallback')
    return result
