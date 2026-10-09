"""Whether Auto can actually run for a project and actor (#6826).

Enablement (policy) stays in routing_settings; readiness says whether the effective
classifier is a deployment this actor can use. Pickers offer Auto only when both hold.
"""
from .llm_model_identity import is_low_tier
from .local_tools import log
from .routing_models import get_routing_models
from .routing_settings import get_effective_settings


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


def usable_classifiers(items, *, shared_only=False):
    return [item for item in items if classifier_problem(item) is None
            and (not shared_only or item.get('shared') is True)]


def classifier_options(items):
    """Shared public chat models for the platform default, low-tier first choice."""
    usable = sorted(usable_classifiers(items, shared_only=True),
                    key=lambda item: (str(item.get('display_name') or item['name']).lower(), item['name']))
    low_tier = [item for item in usable if is_low_tier(item)]
    return [{'name': item['name'], 'project_id': item['project_id'],
             'display_name': item.get('display_name') or item['name'], 'low_tier': is_low_tier(item)}
            for item in (low_tier or usable)]


def _platform_classifier(settings, items):
    ref = settings.get('platform_classifier')
    if not ref:
        return None
    item = find_model(items, ref) or {}
    return {'name': ref['name'], 'project_id': ref['project_id'], 'display_name': item.get('display_name') or ref['name']}


def readiness(settings, items):
    platform_classifier = _platform_classifier(settings, items)
    reasons = []
    if settings.get('enabled') is not True:
        reasons.append({'code': 'AUTO_DISABLED', 'message': 'Auto model selection is disabled for this project'})
    ref = settings.get('classifier')
    if not ref:
        reasons.append({'code': 'CLASSIFIER_NOT_CONFIGURED', 'message': 'No Auto classifier model is configured'})
        return {'ready': False, 'reasons': reasons, 'classifier': None, 'platform_classifier': platform_classifier}
    item = find_model(items, ref)
    problem = classifier_problem(item)
    model = {'name': ref['name'], 'project_id': ref['project_id']}
    if problem == 'CLASSIFIER_UNAVAILABLE':
        reasons.append({'code': problem, 'model': model,
                        'message': f"Classifier model {ref['name']} is no longer available to this project"})
    elif problem == 'CLASSIFIER_NOT_CHAT':
        reasons.append({'code': problem, 'model': model,
                        'message': f"Classifier model {ref['name']} is not a chat model"})
    classifier = {**model, 'display_name': (item or {}).get('display_name') or ref['name'],
                  'source': ref['source'], 'available': bool(item) and item.get('available') is True}
    return {'ready': not reasons, 'reasons': reasons, 'classifier': classifier,
            'platform_classifier': platform_classifier}


def get_auto_routing_readiness(project_id, user_id, settings=None):
    """Trusted internal read; an unreadable inventory leaves the classifier unavailable
    rather than failing the model list that carries this result."""
    settings = settings if settings is not None else get_effective_settings(project_id)
    items = []
    if settings.get('classifier'):
        try:
            items = get_routing_models(project_id, user_id)['items']
        except Exception as exc:  # fail closed: the classifier reads as unavailable
            log.warning(f'Auto readiness could not read the routing inventory: {type(exc).__name__}')
            items = []
    return readiness(settings, items)
