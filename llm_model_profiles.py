"""Reasoning profiles recognized from an LLM model's name (#6819).

The admin form shows only the reasoning settings a model supports. Nothing about the
credential tells the family apart (every front is OpenAI-compatible), so the model name is
the only signal. This module is the single owner of that recognition: the UI renders what
``profiles_payload`` returns and matches names with the same loop as ``recognize_profile``;
saves are bounded by ``check_llm_model_profile_bounds``.

Matching: lowercase the name, turn ``.`` and ``_`` into ``-``, then take the first profile
that has any token as a substring not followed by a digit (so ``gpt-5-2`` does not claim
the dated ``gpt-5-2025-08-07``). Order matters, so specific families come before the bare
family token (``gpt-5-6`` before ``gpt-5``, ``opus-5-5`` before ``opus-5``).

Provider facts behind each row: _investigations/5874/00-provider-practices.md and the
2026-09-29 probe in _investigations/6819/decisions.md (GPT-5.6 ``max`` held out).
"""
import re

from .exceptions import ConfigurationError

PROFILE_LIST_VERSION = 1

EFFORT_LEVELS = ('none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max')
THINKING_TYPES = ('adaptive', 'enabled', 'always_on')
BUDGET_EFFORT_LEVELS = ('low', 'medium', 'high')

# Claude 4.6 to Opus 5 accept thinking off, but the platform has no path that sends it:
# a stored "off" would reach the SDK as a truthy effort. The UI locks the toggle on those
# profiles until the off path ships; the stored data stays provider-accurate.
CLAUDE_OFF_PATH_IMPLEMENTED = False

NAME_NORMALIZATION = {'lowercase': True, 'replace': {'.': '-', '_': '-'}}


def _profile(id_, label, match, *, supports_reasoning=True, thinking_type=None,
             supported_efforts=(), default_effort=None, provider_lock=False, notes=()):
    return {
        'id': id_,
        'label': label,
        'match': tuple(match),
        'supports_reasoning': supports_reasoning,
        'thinking_type': thinking_type,
        'supported_efforts': tuple(supported_efforts),
        'default_effort': default_effort,
        'provider_lock': provider_lock or thinking_type == 'always_on',
        'notes': tuple(notes),
    }


PROFILES = (
    _profile(
        'anthropic-fable-mythos', 'Anthropic Claude Fable / Mythos', ('fable', 'mythos'),
        thinking_type='always_on', supported_efforts=('low', 'medium', 'high', 'xhigh', 'max'),
        default_effort='high',
        notes=('Thinking cannot be turned off for this model.',),
    ),
    _profile(
        'anthropic-opus-5-5', 'Anthropic Claude Opus 5.5', ('opus-5-5',),
        thinking_type='always_on', supported_efforts=('low', 'medium', 'high', 'xhigh', 'max'),
        default_effort='medium',
        notes=('Thinking cannot be turned off for this model.',),
    ),
    _profile(
        'anthropic-adaptive', 'Anthropic Claude Opus 5 / Sonnet 5 / Opus 4.7-4.8',
        ('opus-5', 'sonnet-5', 'opus-4-8', 'opus-4-7'),
        thinking_type='adaptive', supported_efforts=('low', 'medium', 'high', 'xhigh', 'max'),
        default_effort='high',
    ),
    _profile(
        'anthropic-4-6', 'Anthropic Claude Opus 4.6 / Sonnet 4.6', ('opus-4-6', 'sonnet-4-6'),
        thinking_type='adaptive', supported_efforts=('low', 'medium', 'high', 'max'),
        default_effort='high',
        notes=('Claude 4.6 does not accept the xhigh effort level.',),
    ),
    _profile(
        'anthropic-legacy-budget', 'Anthropic Claude 4.5 and older',
        ('claude-3-7', 'haiku-4-5', 'opus-4', 'sonnet-4'),
        thinking_type='enabled', supported_efforts=BUDGET_EFFORT_LEVELS, default_effort='medium',
        notes=('Effort levels map to a thinking token budget (low 2048, medium 4096, high 9092).',),
    ),
    _profile(
        'openai-gpt-5-pro', 'OpenAI GPT-5 pro', ('gpt-5-pro', 'gpt-5-5-pro'),
        supported_efforts=('high',), default_effort='high', provider_lock=True,
        notes=('Only the high effort level is accepted.',),
    ),
    _profile(
        'openai-gpt-5-chat', 'OpenAI GPT-5 chat', ('gpt-5-chat',),
        supports_reasoning=False,
        notes=('gpt-5-chat-latest is a non-reasoning model and rejects reasoning_effort.',),
    ),
    _profile(
        'openai-gpt-5-x-chat', 'OpenAI GPT-5.1 / GPT-5.2 chat', ('gpt-5-1-chat', 'gpt-5-2-chat'),
        supported_efforts=('medium',), default_effort='medium',
        notes=('The chat-tuned 5.1 and 5.2 aliases run a fixed minimal-like reasoning pass; '
               'only medium is documented to be accepted and none is not.',),
    ),
    _profile(
        'openai-gpt-6-astra', 'OpenAI GPT-6 Astra', ('gpt-6-astra',),
        supported_efforts=('low', 'medium', 'high', 'xhigh', 'max'), default_effort='medium',
        provider_lock=True, notes=('Reasoning cannot be turned off for this model.',),
    ),
    _profile(
        'openai-gpt-6', 'OpenAI GPT-6', ('gpt-6',),
        supported_efforts=('none', 'low', 'medium', 'high', 'xhigh', 'max'), default_effort='medium',
    ),
    _profile(
        'openai-gpt-5-6', 'OpenAI GPT-5.6', ('gpt-5-6',),
        supported_efforts=('none', 'low', 'medium', 'high', 'xhigh'), default_effort='medium',
        notes=('The max effort level is accepted by the front but not proven honoured, so it is not offered.',),
    ),
    _profile(
        'openai-gpt-5-5', 'OpenAI GPT-5.5', ('gpt-5-5',),
        supported_efforts=('none', 'low', 'medium', 'high', 'xhigh'), default_effort='medium',
    ),
    _profile(
        'openai-gpt-5-4', 'OpenAI GPT-5.4', ('gpt-5-4',),
        supported_efforts=('none', 'low', 'medium', 'high', 'xhigh'), default_effort='medium',
    ),
    _profile(
        'openai-gpt-5-1-codex-max', 'OpenAI GPT-5.1 Codex Max', ('gpt-5-1-codex-max',),
        supported_efforts=('none', 'low', 'medium', 'high', 'xhigh'), default_effort='medium',
    ),
    _profile(
        'openai-gpt-5-2-5-1', 'OpenAI GPT-5.2 / GPT-5.1', ('gpt-5-2', 'gpt-5-1'),
        supported_efforts=('none', 'low', 'medium', 'high'), default_effort='medium',
        notes=("The provider default for GPT-5.1 is none; the platform default stays medium.",),
    ),
    _profile(
        'openai-gpt-5', 'OpenAI GPT-5 / mini / nano', ('gpt-5',),
        supported_efforts=('minimal', 'low', 'medium', 'high'), default_effort='medium',
        notes=('The original GPT-5 generation has minimal instead of none.',),
    ),
    _profile(
        'openai-gpt-4', 'OpenAI GPT-4 family', ('gpt-4',),
        supports_reasoning=False,
    ),
)


def normalize_model_name(model_name):
    normalized = (model_name or '').lower()
    for source, target in NAME_NORMALIZATION['replace'].items():
        normalized = normalized.replace(source, target)
    return normalized


def token_matches(token, normalized_name):
    return re.search(re.escape(token) + r'(?!\d)', normalized_name) is not None


def recognize_profile(model_name):
    normalized = normalize_model_name(model_name)
    if not normalized:
        return None
    for profile in PROFILES:
        if any(token_matches(token, normalized) for token in profile['match']):
            return profile
    return None


def lock_reason(profile):
    if profile['provider_lock']:
        return 'provider'
    if profile['thinking_type'] == 'adaptive' and not CLAUDE_OFF_PATH_IMPLEMENTED:
        return 'platform'
    return None


def _serialize(profile):
    reason = lock_reason(profile)
    return {
        **profile,
        'match': list(profile['match']),
        'supported_efforts': list(profile['supported_efforts']),
        'notes': list(profile['notes']),
        'locked': reason is not None,
        'lock_reason': reason,
    }


def profiles_payload():
    return {
        'version': PROFILE_LIST_VERSION,
        'matching': {'normalize': NAME_NORMALIZATION,
                     'strategy': 'first_profile_with_any_token_as_substring_not_followed_by_digit'},
        'effort_levels': list(EFFORT_LEVELS),
        'thinking_types': list(THINKING_TYPES),
        'platform': {'claude_off_path_implemented': CLAUDE_OFF_PATH_IMPLEMENTED},
        'profiles': [_serialize(profile) for profile in PROFILES],
    }


def check_llm_model_profile_bounds(config_type, data):
    """Reject reasoning settings a recognized profile rules out; unrecognized names pass.

    The model schema already keeps the fields consistent with each other; this is the
    profile bound the UI applies live, re-applied on save so API clients cannot bypass it.
    """
    if config_type != 'llm_model' or not isinstance(data, dict):
        return
    profile = recognize_profile(data.get('name'))
    if profile is None:
        return
    label = profile['label']
    if not profile['supports_reasoning']:
        if data.get('supports_reasoning'):
            raise ConfigurationError('supports_reasoning', f"{label} does not support reasoning")
        return
    thinking_type = data.get('thinking_type')
    if thinking_type is not None and thinking_type != profile['thinking_type']:
        expected = profile['thinking_type'] or 'no thinking mode'
        raise ConfigurationError('thinking_type', f"{label} uses {expected}, not {thinking_type}")
    unsupported = [level for level in data.get('supported_efforts') or () if level not in profile['supported_efforts']]
    if unsupported:
        raise ConfigurationError(
            'supported_efforts', f"{label} does not support effort level(s): {', '.join(unsupported)}"
        )
