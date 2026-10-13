"""Whether Auto can actually run for a project and actor (#6826).

Enablement (policy) stays in routing_settings; readiness says whether the effective
classifier is a deployment this actor can use. Pickers offer Auto only when both hold.
"""
from .llm_model_identity import is_low_tier
from .routing_settings import (
    classifier_problem, classifier_reason, find_model, get_classifier_state, resolve_classifier,
)


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


def _described(ref, items):
    item = find_model(items, ref) or {}
    return {'name': ref['name'], 'project_id': ref['project_id'],
            'display_name': item.get('display_name') or ref['name'], 'source': ref['source']}


# Informational: reported to admins but never block Auto (the effective policy already fails closed).
ADVISORY_CODES = ('POLICY_INVALID_STORED', 'ACTIVATION_WITHOUT_ARTIFACT')
ISSUE_MESSAGES = {
    ('POLICY_INVALID_STORED', 'platform'): 'The stored platform Auto routing policy is invalid; built-in defaults are used',
    ('POLICY_INVALID_STORED', 'project'): 'The project Auto routing policy override is invalid and is ignored',
    ('ACTIVATION_WITHOUT_ARTIFACT', 'policy'):
        'The Auto routing policy leaves shadow mode without a pinned selector artifact; shadow mode stays on',
}


def models_auto_routing(gates, refs, items):
    """The ``auto_routing`` block of the models listing."""
    return {'enabled': gates['enabled'], 'revision': gates['revision'],
            'profile_ref': {'id': 'v7-quality-cost', 'revision': 1},
            'policy': gates['policy'], 'policy_source': gates['policy_source'],
            'readiness': readiness(gates, refs, items)}


def readiness(settings, refs, items):
    """Reasons describe the finally used level; skipped levels are not reported (#6826 A1)."""
    default = resolve_classifier([(source, ref) for source, ref in refs if source != 'project'], items)
    reasons = []
    if settings.get('enabled') is not True:
        reasons.append({'code': 'AUTO_DISABLED', 'message': 'Auto model selection is disabled for this project'})
    fallback = settings.get('fallback_reason')
    used = settings.get('classifier')
    reason = classifier_reason(used, refs, items)
    if reason:
        reasons.append(reason)
    # ready is false for an unresolvable policy.fallback only; an unset fallback or selector adds no reason.
    if fallback:
        reasons.append(fallback)
    for issue in settings.get('policy_issues') or []:
        reasons.append({'code': issue['code'], 'message': ISSUE_MESSAGES[(issue['code'], issue['source'])]})
    if used:
        classifier = {**_described(used, items), 'available': True}
    elif refs:
        source, ref = refs[0]
        classifier = {**_described({**ref, 'source': source}, items), 'available': False}
    else:
        classifier = None
    return {'ready': not [r for r in reasons if r['code'] not in ADVISORY_CODES], 'reasons': reasons, 'classifier': classifier,
            'default_classifier': default and _described(default, items)}


def get_auto_routing_readiness(project_id, user_id):
    """Trusted internal read; an unreadable inventory leaves the classifier unavailable
    rather than failing the model list that carries this result."""
    settings, refs, items = get_classifier_state(project_id, user_id, check_availability=True)
    return readiness(settings, refs, items)
