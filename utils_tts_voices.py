"""
Utility functions for fetching TTS voices from major providers.

LiteLLM-managed providers (OpenAI, Azure OpenAI, Google/Vertex AI) share a fixed set of
OpenAI-compatible voice IDs because LiteLLM translates them to the underlying provider's
equivalents at inference time.  No provider API call is needed for these.

All other vendors (ElevenLabs, AWS Polly, Deepgram, PlayHT, IBM Watson, Azure Cognitive
Services TTS) expose their own voice catalogues and require a live API fetch.

Static voice data (LiteLLM standard/realtime voices, Deepgram Aura voices) is loaded from
``data/tts_voices.json`` at import time so it can be updated without code changes.
"""
import json
import pathlib
from pylon.core.tools import log

from .utils_tts_providers import (
    fetch_voices_from_aws_polly,
    fetch_voices_from_azure_cognitive,
    fetch_voices_from_deepgram,
    fetch_voices_from_elevenlabs,
    fetch_voices_from_ibm_watson,
    fetch_voices_from_playht,
)

# ---------------------------------------------------------------------------
# Load static voice data from configuration file
# ---------------------------------------------------------------------------
_VOICES_DATA_FILE = pathlib.Path(__file__).parent / 'data' / 'tts_voices.json'

try:
    _VOICES_DATA: dict = json.loads(_VOICES_DATA_FILE.read_text(encoding='utf-8'))
except Exception as _load_err:
    log.warning("Could not load TTS voice data from %s: %s", _VOICES_DATA_FILE, _load_err)
    _VOICES_DATA = {}

# ---------------------------------------------------------------------------
# Public voice list constants (consumed by external callers)
# ---------------------------------------------------------------------------

# OpenAI / Azure OpenAI TTS voices (9 standard + 2 realtime).
LITELLM_TTS_VOICES: list[dict] = _VOICES_DATA.get('openai_tts', [])
LITELLM_REALTIME_TTS_VOICES: list[dict] = (
    LITELLM_TTS_VOICES + _VOICES_DATA.get('openai_realtime_extra', [])
)

# Google Gemini TTS native voices.  LiteLLM does NOT translate OpenAI voice
# IDs on the Gemini path — only native Gemini names are accepted.
GEMINI_TTS_VOICES: list[dict] = _VOICES_DATA.get('gemini_tts', [])

# Classic Google Cloud TTS / Vertex AI (non-Gemini).  LiteLLM maps only 6 of
# the 9 OpenAI voices on this path; the remaining three (ash, coral, sage)
# are passed through literally and rejected by the Google API.
VERTEX_TTS_VOICES: list[dict] = _VOICES_DATA.get('vertex_tts_mapped', [])

# Deepgram Aura static catalogue.
_DEEPGRAM_AURA_VOICES: list[dict] = _VOICES_DATA.get('deepgram_aura', [])


# ---------------------------------------------------------------------------
# LiteLLM helper
# ---------------------------------------------------------------------------

def _get_openai_voices_for_model(model: str) -> list[dict]:
    """Return the OpenAI voice list appropriate for *model*.

    Realtime models expose two additional voices on top of the standard nine.
    """
    if 'realtime' in model.lower().replace('_', '-'):
        return LITELLM_REALTIME_TTS_VOICES
    return LITELLM_TTS_VOICES


def _get_google_voices_for_model(model: str) -> list[dict]:
    """Return the correct Google-family voice list based on the model name.

    - Gemini models (name contains 'gemini'): return native Gemini voice IDs.
      LiteLLM has no mapping table on the Gemini code path; OpenAI names fail.
    - Classic Vertex AI / Google Cloud TTS: return only the 6 OpenAI voice IDs
      that LiteLLM actually maps.  The other 3 (ash, coral, sage) are passed
      through literally and cause a 400 from the Google API.
    """
    if 'gemini' in model.lower():
        return GEMINI_TTS_VOICES
    return VERTEX_TTS_VOICES


# ---------------------------------------------------------------------------
# Provider dispatch registry
#
# Each entry is a tuple of:
#   (match_predicate, fetch_callable)
#
# ``match_predicate`` receives the normalised config type string (lowercase,
# underscores removed) and returns True if this entry handles that type.
# ``fetch_callable`` receives (data, model) and returns list[dict].
#
# Entries are checked in order; the first match wins.
# ---------------------------------------------------------------------------

_TTS_PROVIDER_REGISTRY: list[tuple] = [
    # ------------------------------------------------------------------
    # LiteLLM-managed providers — return fixed voice list, no API call
    # ------------------------------------------------------------------

    # OpenAI (plain or via LiteLLM proxy); Azure OpenAI is caught below
    (
        lambda t: t == 'openai' or ('openai' in t and 'azure' not in t),
        lambda data, model: _get_openai_voices_for_model(model),
    ),
    # Azure OpenAI — same OpenAI voice IDs, LiteLLM passes them natively
    (
        lambda t: 'azure' in t and ('openai' in t or t in ('azure', 'azureopenai')),
        lambda data, model: _get_openai_voices_for_model(model),
    ),
    # Google Gemini (Google AI Studio path — model name contains 'gemini')
    # Dispatch before the generic Vertex entry so it takes precedence.
    (
        lambda t: t in ('google', 'googleai', 'gemini') or 'gemini' in t,
        lambda data, model: _get_google_voices_for_model(model),
    ),
    # Classic Google Cloud TTS / Vertex AI (non-Gemini)
    # Fixes dead condition: the previous single entry used `'google' in t`
    # twice (redundant) and returned the OpenAI-9 list for all Google paths.
    (
        lambda t: any(k in t for k in ('vertex', 'gcp', 'google')),
        lambda data, model: _get_google_voices_for_model(model),
    ),
    # ------------------------------------------------------------------
    # Vendor-specific providers — fetch voices from their APIs
    # ------------------------------------------------------------------
    (
        lambda t: t in ('azuretts', 'azurecognitive'),
        lambda data, model: fetch_voices_from_azure_cognitive(data),
    ),
    (
        lambda t: t == 'aws' or 'polly' in t,
        lambda data, model: fetch_voices_from_aws_polly(data),
    ),
    (
        lambda t: 'elevenlabs' in t or 'eleven' in t,
        lambda data, model: fetch_voices_from_elevenlabs(data),
    ),
    (
        lambda t: 'deepgram' in t,
        lambda data, model: fetch_voices_from_deepgram(data, _DEEPGRAM_AURA_VOICES),
    ),
    (
        lambda t: 'playht' in t or 'playai' in t,
        lambda data, model: fetch_voices_from_playht(data),
    ),
    (
        lambda t: 'ibm' in t or 'watson' in t,
        lambda data, model: fetch_voices_from_ibm_watson(data),
    ),
]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch_tts_voices(config_data: dict) -> list[dict]:
    """Return available voices for the TTS configuration described by *config_data*.

    Strategy
    --------
    The provider type is resolved from ``config_data`` (or from the nested
    ``ai_credentials`` dict for generic ``tts_model`` entries), then looked up
    in ``_TTS_PROVIDER_REGISTRY``.

    LiteLLM-managed providers (OpenAI, Azure OpenAI, Google/Vertex AI) return a
    fixed list loaded from ``data/tts_voices.json`` — no live API call needed.

    All other vendors (ElevenLabs, AWS Polly, Deepgram, PlayHT, IBM Watson,
    Azure Cognitive Services TTS) are fetched from their provider APIs via the
    functions in ``utils_tts_providers``.

    Input format
    ------------
    Accepts both wrapped ``{'data': {...}}`` (from event handlers) and flat
    ``{...}`` (from check-connection API) dicts.

    Returns
    -------
    List of ``{'id': ..., 'name': ...}`` dicts, or ``[]`` on error / unknown
    provider.
    """
    # ------------------------------------------------------------------
    # Resolve vault secret references in config_data (safety net — callers
    # such as check_connection and configuration_created already call
    # VaultClient.unsecret() before reaching here, but guard against direct
    # callers that skip that step).
    # ------------------------------------------------------------------
    project_id = config_data.get('project_id')
    if project_id:
        try:
            from .local_tools import VaultClient
            vc = VaultClient(project_id)
            config_data = vc.unsecret(config_data)
        except Exception as _vc_err:
            log.warning("Could not unsecret TTS config (project_id=%s): %s", project_id, _vc_err)

    # ------------------------------------------------------------------
    # Normalise input: extract 'data' sub-dict and top-level config_type
    # ------------------------------------------------------------------
    if 'data' in config_data and isinstance(config_data['data'], dict):
        data = config_data['data']
        config_type = (config_data.get('configuration_type') or config_data.get('type', '')).lower()
    else:
        data = config_data
        config_type = (config_data.get('configuration_type') or config_data.get('type', '')).lower()

    model = data.get('name', '')

    # For generic 'tts_model' entries the actual provider type lives inside
    # ai_credentials (populated by expand_configuration).
    if not config_type or config_type == 'tts_model':
        ai_creds = data.get('ai_credentials', {})
        if isinstance(ai_creds, dict):
            config_type = (ai_creds.get('configuration_type') or ai_creds.get('type', '')).lower()
            if config_type:
                log.info("Detected TTS provider type from ai_credentials: %s", config_type)
                data = {**data, **ai_creds}
            else:
                log.warning(
                    "ai_credentials present but no configuration_type: keys=%s",
                    list(ai_creds.keys()),
                )

    if not config_type:
        log.warning(
            "Could not determine TTS provider type.  "
            "config_data keys=%s  data keys=%s",
            list(config_data.keys()), list(data.keys()),
        )
        return []

    t = config_type.replace('_', '')  # normalised type string for matching

    try:
        for match, fetch in _TTS_PROVIDER_REGISTRY:
            if match(t):
                return fetch(data, model)

        log.warning("Unknown TTS provider type: %s", config_type)
        return []

    except Exception as e:
        log.error("Error fetching TTS voices for %s: %s", config_type, e)
        return []
