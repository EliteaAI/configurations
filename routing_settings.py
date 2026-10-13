"""Read current routing gates from their existing configuration owners."""
import hashlib
import json

from pydantic import ValidationError

from .common_utils import get_public_project_id
from .models.pd.auto_routing import (
    AutoRoutingPolicy, AutoRoutingPolicyOverride, ClassifierRef, budgets_increasing, routing_enabled,
)
from .utils_getters import get_project_configuration


def _data(record):
    data = record.get('data') if isinstance(record, dict) else None
    return data if isinstance(data, dict) else {}


LOW_TIER_KEYS = ('default_llm_low_tier_model_name', 'default_llm_low_tier_model_project_id')


def _ref(value):
    if value is None:
        return None
    try:
        return ClassifierRef.model_validate(value).model_dump()
    except ValidationError:
        return None


def low_tier_ref(secrets):
    """The project's Low-tier default model (AI Providers), stored as two vault keys."""
    secrets = secrets if isinstance(secrets, dict) else {}
    name, project_id = (secrets.get(key) for key in LOW_TIER_KEYS)
    if isinstance(project_id, str) and project_id.strip().isdigit():
        project_id = int(project_id)
    return _ref({'name': name, 'project_id': project_id}) if name and project_id else None


def classifier_refs(env_data, project_data, low_tier=None):
    """Configured classifier references by priority (#6826 A1); malformed ones are skipped."""
    refs = [('project', _ref(project_data.get('classifier'))), ('project_low_tier', _ref(low_tier)),
            ('platform', _ref(env_data.get('auto_routing_classifier')))]
    return [(source, ref) for source, ref in refs if ref]


def find_model(items, ref):
    for item in items:
        if item.get('name') == ref['name'] and item.get('project_id') == ref['project_id']:
            return item
    return None


def classifier_problem(item):
    if not item or item.get('available') is not True:
        return 'CLASSIFIER_UNAVAILABLE'
    if (item.get('identity') or {}).get('kind') != 'chat':
        return 'CLASSIFIER_NOT_CHAT'
    return None


def resolve_classifier(refs, items=None):
    """First reference that is an available chat model in ``items``.

    Without an inventory (no actor) the first configured reference is returned unchecked.
    """
    for source, ref in refs:
        if items is None or classifier_problem(find_model(items, ref)) is None:
            return {**ref, 'source': source}
    return None


def classifier_reason(resolved, refs, items):
    """None when a classifier resolved; otherwise the reason for the highest-priority configured level."""
    if resolved:
        return None
    if not refs:
        return {'code': 'CLASSIFIER_NOT_CONFIGURED', 'message': 'No Auto classifier model is configured',
                'model': None}
    ref = refs[0][1]
    code = classifier_problem(find_model(items or [], ref))
    message = (f"Classifier model {ref['name']} is not a chat model" if code == 'CLASSIFIER_NOT_CHAT'
               else f"Classifier model {ref['name']} is no longer available to this project")
    return {'code': code, 'message': message, 'model': dict(ref)}


def fallback_reason(policy, items):
    """None when no fallback is configured, the inventory is unknown (no actor) or the fallback resolves."""
    ref = _ref(policy.get('fallback'))
    if not ref or items is None:
        return None
    problem = classifier_problem(find_model(items, ref))
    if problem is None:
        return None
    message = (f"Fallback model {ref['name']} is not a chat model" if problem == 'CLASSIFIER_NOT_CHAT'
               else f"Fallback model {ref['name']} is no longer available to this project")
    return {'code': 'FALLBACK_UNAVAILABLE', 'message': message, 'model': dict(ref)}


# --- Auto routing policy (#6722) -----------------------------------------------------------

NESTED_POLICY_KEYS = ('selector', 'output', 'thinking_budget_tokens', 'classifier')
ALLOW_ACTIVATION = 'auto_routing_policy_allow_project_activation'


def _valid(model, value):
    if value is None:
        return None
    try:
        return model.model_validate(value)
    except ValidationError:
        return None


def platform_policy(env_data):
    """(complete platform policy, allow_project_activation, stored?) - an absent or malformed policy is the defaults."""
    stored = _valid(AutoRoutingPolicy, env_data.get('auto_routing_policy'))
    return (stored or AutoRoutingPolicy()).model_dump(mode='json'), env_data.get(ALLOW_ACTIVATION) is True, stored is not None


def policy_resolution(environment, project):
    """Merge the platform policy with the project override: (policy, policy_source, issues).

    Per top-level key, an override wins; nested objects merge field by field and lists are replaced.
    Platform limits hold at merge time as well as at write time:
    - a project cannot exceed the platform ``output.max_cap``; ``default_allowance`` is clamped into
      [min_cap, max_cap];
    - a project leaves shadow mode only when the platform policy is not in shadow mode or allows project
      activation; an effective policy without a pinned selector artifact stays in shadow mode;
    - thinking budgets that are not strictly increasing fall back to the platform budgets.
    A malformed stored platform policy serves the defaults, a malformed override is ignored as a whole.
    ``issues`` are advisory codes for readiness: POLICY_INVALID_STORED, ACTIVATION_WITHOUT_ARTIFACT.
    """
    env_data, project_data = _data(environment), _data(project)
    issues = []
    policy, allow_activation, stored = platform_policy(env_data)
    if not stored and env_data.get('auto_routing_policy') is not None:
        issues.append({'code': 'POLICY_INVALID_STORED', 'source': 'platform'})
    base_source = 'platform' if stored else 'default'
    source = dict.fromkeys(policy, base_source)
    platform_max_cap = policy['output']['max_cap']
    platform_budgets = dict(policy['thinking_budget_tokens'])
    override = _valid(AutoRoutingPolicyOverride, project_data.get('policy'))
    if not override and project_data.get('policy') is not None:
        issues.append({'code': 'POLICY_INVALID_STORED', 'source': 'project'})
    for key, value in (override.model_dump(mode='json', exclude_unset=True) if override else {}).items():
        if key == 'shadow_mode' and value is False and policy[key] is True and not allow_activation:
            continue
        policy[key] = {**policy[key], **value} if key in NESTED_POLICY_KEYS else value
        source[key] = 'project'
    output = policy['output']
    output['max_cap'] = min(output['max_cap'], platform_max_cap)
    output['min_cap'] = min(output['min_cap'], output['max_cap'])
    output['default_allowance'] = max(output['min_cap'], min(output['default_allowance'], output['max_cap']))
    if not budgets_increasing(policy['thinking_budget_tokens']):
        policy['thinking_budget_tokens'] = platform_budgets
        source['thinking_budget_tokens'] = base_source
    if policy['shadow_mode'] is False and policy['selector']['artifact'] is None:
        policy['shadow_mode'] = True
        source['shadow_mode'] = base_source
        issues.append({'code': 'ACTIVATION_WITHOUT_ARTIFACT', 'source': 'policy'})
    return policy, source, issues


def effective_policy(environment, project):
    """(policy, policy_source) of :func:`policy_resolution`."""
    return policy_resolution(environment, project)[:2]


def effective_settings(environment, project, low_tier=None, items=None):
    """Only literal booleans grant availability; malformed records fail closed.

    This revision identifies availability settings, not a published routing
    profile or authorization token. No model/credential metadata is exposed.
    """
    env_data, project_data = _data(environment), _data(project)
    fields = {
        'available': env_data.get('auto_routing_available') is True,
        'project_default': env_data.get('auto_routing_project_default') is True,
        'project_override': project_data.get('enabled'),
    }
    malformed = fields['project_override'] is not None and type(fields['project_override']) is not bool
    if malformed:
        fields['project_override'] = False
    fields['enabled'] = routing_enabled(env_data, project_data)
    # Project classifier, else project Low-tier model, else platform classifier (#6826 A1); covered by revision.
    refs = classifier_refs(env_data, project_data, low_tier)
    fields['classifier'] = resolve_classifier(refs, items)
    # Platform policy merged with the project override; canonical JSON below makes it part of revision.
    fields['policy'], policy_source, policy_issues = policy_resolution(environment, project)
    fields['revision'] = hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()
    # Why no classifier resolved; derived from the resolution above, so not part of revision.
    fields['classifier_reason'] = classifier_reason(fields['classifier'], refs, items)
    fields['policy_source'] = policy_source
    fields['policy_issues'] = policy_issues
    fields['fallback_reason'] = fallback_reason(fields['policy'], items)
    return fields


def _project_secrets(project_id):
    from .local_tools import VaultClient
    return VaultClient.from_project(project_id).get_secrets()


def _inventory(project_id, user_id):
    """Actor-visible routing models; an unreadable inventory leaves every classifier unusable."""
    from .routing_models import get_routing_models
    try:
        return get_routing_models(project_id, user_id)['items']
    except Exception as exc:  # fail closed
        from .local_tools import log
        log.warning(f'Auto classifier could not read the routing inventory: {type(exc).__name__}')
        return []


def get_classifier_state(project_id, user_id=None, *, check_availability=False):
    """Return (settings, configured classifier refs, actor inventory or None)."""
    platform = get_project_configuration(get_public_project_id(), {
        'type': 'environment_settings', 'elitea_title': 'environment_settings',
    })
    project = get_project_configuration(project_id, {
        'type': 'auto_routing', 'elitea_title': 'auto_routing',
    })
    low_tier = low_tier_ref(_project_secrets(project_id))
    refs = classifier_refs(_data(platform), _data(project), low_tier)
    fallback = effective_policy(platform, project)[0]['fallback']
    items = None
    if check_availability or user_id is not None:
        items = _inventory(project_id, user_id) if refs or fallback else []
    return effective_settings(platform, project, low_tier, items), refs, items


def get_effective_settings(project_id, user_id=None):
    """Internal trusted-project read; public callers require endpoint RBAC.

    Read current flags rather than caching an enable decision across revocation.
    Callers on the fixed path need not invoke this service. With ``user_id`` the
    classifier is the first level available to that actor; without it, the first
    configured level (unchecked).
    """
    return get_classifier_state(project_id, user_id)[0]
