"""Read current routing gates from their existing configuration owners."""
import hashlib
import json

from pydantic import ValidationError

from .common_utils import get_public_project_id
from .models.pd.auto_routing import ClassifierRef, routing_enabled
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
    fields['revision'] = hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()
    # Why no classifier resolved; derived from the resolution above, so not part of revision.
    fields['classifier_reason'] = classifier_reason(fields['classifier'], refs, items)
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
    items = None
    if check_availability or user_id is not None:
        items = _inventory(project_id, user_id) if refs else []
    return effective_settings(platform, project, low_tier, items), refs, items


def get_effective_settings(project_id, user_id=None):
    """Internal trusted-project read; public callers require endpoint RBAC.

    Read current flags rather than caching an enable decision across revocation.
    Callers on the fixed path need not invoke this service. With ``user_id`` the
    classifier is the first level available to that actor; without it, the first
    configured level (unchecked).
    """
    return get_classifier_state(project_id, user_id)[0]
