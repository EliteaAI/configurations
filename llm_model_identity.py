"""Canonical model identity derived from a deployment name (#6826).

The same model is deployed under different names per environment (DIAL
``gpt-5.6-luna-2026-07-09``, Bedrock ``eu.anthropic.claude-haiku-4-5-20251001-v1:0``,
Vertex ``claude-haiku-5-5@default``). Consumers join on ``vendor/family`` instead of the raw
name. This is a pure function of (name, api_protocol, explicit override); nothing is
persisted except the optional ``canonical_model`` override on the LLM model configuration.
A canonical match makes a deployment eligible to join a measured contract; it is not
evidence that two deployments behave the same.

Rules are ordered and deterministic (handoff PLAN §3.2); never read credentials here.
"""
import re

from .llm_model_profiles import normalize_model_name

IDENTITY_VERSION = 1
VENDORS = (
    'openai', 'anthropic', 'google', 'meta', 'deepseek', 'xai', 'qwen', 'zai', 'minimax', 'moonshot',
    'nvidia', 'mistral', 'stability', 'microsoft',
)
CANONICAL_RE = re.compile(r'^([a-z]+)/([a-z0-9][a-z0-9-]*)$')
CANONICAL_MAX_LENGTH = 128
LOW_TIER_TOKENS = ('haiku', 'luna', 'flash', 'lite', 'mini', 'nano')

_OWNER_PREFIX = re.compile(r'^\d+_')
_PROVIDER_PATH = re.compile(r'^(?:openai|azure|anthropic|bedrock|vertex_ai|gemini|google|responses)/')
_REGION_PREFIX = re.compile(r'^(?:global|eu|us|apac|ca|au|jp)\.')
_VENDOR_PREFIX = re.compile(
    r'^(openai|anthropic|amazon|meta|google|gemini|qwen|zai|xai|nvidia|deepseek|minimax|moonshotai|'
    r'stability|foundry|azure)\.'
)
_PREFIX_VENDOR = {
    'openai': 'openai', 'anthropic': 'anthropic', 'meta': 'meta', 'google': 'google', 'gemini': 'google',
    'qwen': 'qwen', 'zai': 'zai', 'xai': 'xai', 'nvidia': 'nvidia', 'deepseek': 'deepseek',
    'minimax': 'minimax', 'moonshotai': 'moonshot', 'stability': 'stability',
}
_CONTRACT_SUFFIX = re.compile(r'-(reasoning|with-thinking|without-thinking|websearch|google-search)$')
_CONTRACTS = {
    'reasoning': 'reasoning', 'with-thinking': 'reasoning', 'without-thinking': 'default',
    'websearch': 'websearch', 'google-search': 'websearch',
}
_VERSION_PINS = (
    re.compile(r'@default$'), re.compile(r'@\d{8}$'), re.compile(r'-\d{4}-\d{2}-\d{2}$'),
    re.compile(r'-\d{8}$'), re.compile(r'-v1(?::\d+)?$'), re.compile(r'-latest$'), re.compile(r'-\d{3}$'),
)
_PREVIEW = re.compile(r'-preview$')
_FAMILY_VENDOR = (
    (('gpt', 'o1', 'o3', 'o4', 'chatgpt'), 'openai'), (('claude',), 'anthropic'), (('gemini', 'veo'), 'google'),
    (('llama',), 'meta'), (('deepseek',), 'deepseek'), (('grok',), 'xai'), (('qwen',), 'qwen'), (('glm',), 'zai'),
    (('minimax',), 'minimax'), (('kimi',), 'moonshot'), (('nemotron',), 'nvidia'),
    (('mistral', 'mixtral', 'codestral'), 'mistral'),
)
_FAMILY_RE = re.compile(r'^[a-z0-9][a-z0-9-]*$')


def _strip_repeatedly(value, patterns):
    changed = True
    while changed:
        changed = False
        for pattern in patterns:
            stripped = pattern.sub('', value, count=1)
            if stripped != value:
                value, changed = stripped, True
    return value


def _kind(family):
    if 'embedding' in family or 'embed' in family:
        return 'embedding'
    if 'image' in family or 'stable-' in family or 'sd3' in family:
        return 'image'
    if 'tts' in family or 'whisper' in family or 'transcribe' in family:
        return 'audio'
    if 'veo' in family:
        return 'video'
    return 'chat'


def _family_vendor(family):
    for segment in family.split('-'):
        for tokens, vendor in _FAMILY_VENDOR:
            if any(re.match(re.escape(token) + r'(?![a-z])', segment) for token in tokens):
                return vendor
    return None


def _low_tier_hint(family):
    return any(token in LOW_TIER_TOKENS for token in family.split('-'))


def parse_canonical(value):
    """Return (vendor, family) for a grammar-valid canonical with a known vendor, else None."""
    if not isinstance(value, str) or len(value) > CANONICAL_MAX_LENGTH:
        return None
    match = CANONICAL_RE.fullmatch(value)
    if not match or match.group(1) not in VENDORS:
        return None
    return match.group(1), match.group(2)


def resolve_identity(name, *, api_protocol=None, explicit=None):
    """PLAN §3.1 identity object for one deployment name."""
    value = name.strip().lower() if isinstance(name, str) else ''
    value = _OWNER_PREFIX.sub('', value, count=1)
    value = _strip_repeatedly(value, (_PROVIDER_PATH,))
    hint = None
    while True:
        region = _REGION_PREFIX.match(value)
        prefix = _VENDOR_PREFIX.match(value)
        if region:
            value = value[region.end():]
        elif prefix:
            hint = hint or _PREFIX_VENDOR.get(prefix.group(1))
            value = value[prefix.end():]
        else:
            break
    if value.startswith('fw-'):
        value = value[3:]

    contract = 'default'
    match = _CONTRACT_SUFFIX.search(value.replace('_', '-').replace('.', '-'))
    if match:
        contract = _CONTRACTS[match.group(1)]
        value = value[:match.start()]

    preview = False
    while True:
        value = _strip_repeatedly(value, _VERSION_PINS)
        stripped = _PREVIEW.sub('', value, count=1)
        if stripped == value:
            break
        value, preview = stripped, True

    family = re.sub(r'-{2,}', '-', normalize_model_name(value)).strip('-')
    family = re.sub(r'^claude-v(\d)', r'claude-\1', family)

    override = parse_canonical(explicit)
    if override:
        vendor, family = override
        return {
            'version': IDENTITY_VERSION, 'canonical': f'{vendor}/{family}', 'vendor': vendor, 'family': family,
            'kind': 'chat', 'contract': contract, 'low_tier_hint': _low_tier_hint(family), 'preview': preview,
            'source': 'explicit',
        }

    if not _FAMILY_RE.fullmatch(family):
        return {
            'version': IDENTITY_VERSION, 'canonical': None, 'vendor': None, 'family': None, 'kind': 'unknown',
            'contract': contract, 'low_tier_hint': False, 'preview': preview, 'source': 'unresolved',
        }

    kind = _kind(family)
    vendor = hint or _family_vendor(family) or ('anthropic' if api_protocol == 'anthropic' else None)
    canonical = f'{vendor}/{family}' if vendor and kind == 'chat' else None
    if canonical and len(canonical) > CANONICAL_MAX_LENGTH:
        canonical = None
    return {
        'version': IDENTITY_VERSION, 'canonical': canonical, 'vendor': vendor, 'family': family, 'kind': kind,
        'contract': contract, 'low_tier_hint': kind == 'chat' and _low_tier_hint(family), 'preview': preview,
        'source': 'unresolved' if kind == 'chat' and canonical is None else 'rule',
    }


def is_low_tier(item):
    """Admin Low-tier flag, or a name that reads as a fast, low-cost family."""
    identity = item.get('identity') or {}
    return item.get('low_tier') is True or identity.get('low_tier_hint') is True
