"""
Utility functions for fetching TTS voices from major providers.

LiteLLM-managed providers (OpenAI, Azure OpenAI, Google/Vertex AI) share a fixed set of
OpenAI-compatible voice IDs because LiteLLM translates them to the underlying provider's
equivalents at inference time.  No provider API call is needed for these.

All other vendors (ElevenLabs, AWS Polly, Deepgram, PlayHT, IBM Watson, Azure Cognitive
Services TTS) expose their own voice catalogues and require a live API fetch.
"""
from typing import Optional
from pylon.core.tools import log

# ---------------------------------------------------------------------------
# Fixed voice lists for LiteLLM-managed providers
# ---------------------------------------------------------------------------

# Standard OpenAI TTS voices – also used by LiteLLM when routing to Azure OpenAI
# or Google/Vertex AI (LiteLLM translates the voice ID at the proxy layer).
LITELLM_TTS_VOICES: list[dict] = [
    {"id": "alloy",   "name": "Alloy"},
    {"id": "ash",     "name": "Ash"},
    {"id": "coral",   "name": "Coral"},
    {"id": "echo",    "name": "Echo"},
    {"id": "fable",   "name": "Fable"},
    {"id": "nova",    "name": "Nova"},
    {"id": "onyx",    "name": "Onyx"},
    {"id": "sage",    "name": "Sage"},
    {"id": "shimmer", "name": "Shimmer"},
]

# Realtime models expose two extra voices on top of the standard nine.
LITELLM_REALTIME_TTS_VOICES: list[dict] = LITELLM_TTS_VOICES + [
    {"id": "ballad", "name": "Ballad"},
    {"id": "verse",  "name": "Verse"},
]


def _get_litellm_voices_for_model(model: str) -> list[dict]:
    """Return the fixed LiteLLM voice list appropriate for *model*.

    Realtime models expose two additional voices; all others use the standard
    nine OpenAI-compatible voices.
    """
    if 'realtime' in model.lower().replace('_', '-'):
        return LITELLM_REALTIME_TTS_VOICES
    return LITELLM_TTS_VOICES

def _ensure_protocol(url: str) -> str:
    """Ensure URL has http:// or https:// protocol."""
    if not url:
        return url
    url = url.strip()
    if not url.startswith(('http://', 'https://')):
        return f'https://{url}'
    return url

def fetch_voices_from_openai(model: str = "tts-1", **_kwargs) -> list[dict]:
    """Return the fixed voice list for OpenAI TTS models.

    OpenAI does not provide an API endpoint to enumerate voices; the list is
    published in the documentation and is stable.  LiteLLM routes these same
    voice IDs to Azure OpenAI and Google/Vertex AI, so no credential
    validation call is required here.
    """
    return _get_litellm_voices_for_model(model)


def fetch_voices_from_azure(model: str = "tts", **_kwargs) -> list[dict]:
    """Return the fixed voice list for Azure OpenAI TTS models.

    When routed through LiteLLM, Azure OpenAI accepts the same standard
    OpenAI voice IDs (LiteLLM maps them internally).  No live API call is
    needed to discover this fixed set.
    """
    return _get_litellm_voices_for_model(model)


def fetch_voices_from_google_cloud(model: str = "", **_kwargs) -> list[dict]:
    """Return the fixed voice list for Google/Vertex AI TTS via LiteLLM.

    When accessed through LiteLLM, Google Cloud TTS and Vertex AI accept the
    standard OpenAI voice IDs (LiteLLM translates them to native Google
    voices at the proxy layer).  The native Google catalogue contains hundreds
    of voices, but only the LiteLLM-mapped set is usable through the platform.
    """
    return _get_litellm_voices_for_model(model)


def fetch_voices_from_aws_polly(access_key: str, secret_key: str, region: str = "us-east-1") -> list[dict]:
    """
    Fetch voices from AWS Polly.
    
    AWS Polly has a describe_voices() API.
    """
    try:
        import boto3
        
        client = boto3.client(
            'polly',
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region
        )
        
        response = client.describe_voices()
        
        voices = []
        for voice in response.get('Voices', []):
            voices.append({
                "id": voice['Id'],
                "name": f"{voice['Name']} ({voice['LanguageName']}, {voice['Gender']})"
            })
        
        return voices
    except ImportError:
        log.warning("boto3 (AWS SDK) not installed")
        return []
    except Exception as e:
        log.error(f"Error fetching AWS Polly voices: {e}")
        return []


def fetch_voices_from_elevenlabs(api_key: str) -> list[dict]:
    """
    Fetch voices from ElevenLabs.
    
    ElevenLabs provides an API to list all available voices for the account.
    """
    try:
        import requests
        
        response = requests.get(
            "https://api.elevenlabs.io/v1/voices",
            headers={"xi-api-key": api_key}
        )
        response.raise_for_status()
        
        data = response.json()
        voices = []
        
        for voice in data.get('voices', []):
            # Include category if available
            category = voice.get('category', '')
            name = voice['name']
            if category:
                name = f"{name} ({category})"
            
            voices.append({
                "id": voice['voice_id'],
                "name": name
            })
        
        return voices
    except Exception as e:
        log.error(f"Error fetching ElevenLabs voices: {e}")
        return []


def fetch_voices_from_deepgram(api_key: str) -> list[dict]:
    """
    Fetch voices from Deepgram TTS (Aura).
    
    Deepgram Aura supports multiple voices for different languages.
    """
    try:
        # Deepgram Aura voices (as of 2024)
        # Validate API key by making a simple request
        import requests
        
        # Test the API key with a minimal request
        response = requests.get(
            "https://api.deepgram.com/v1/projects",
            headers={"Authorization": f"Token {api_key}"}
        )
        response.raise_for_status()
        
        # Return Deepgram Aura voices
        return [
            {"id": "aura-asteria-en", "name": "Asteria (English)"},
            {"id": "aura-luna-en", "name": "Luna (English)"},
            {"id": "aura-stella-en", "name": "Stella (English)"},
            {"id": "aura-athena-en", "name": "Athena (English)"},
            {"id": "aura-hera-en", "name": "Hera (English)"},
            {"id": "aura-orion-en", "name": "Orion (English)"},
            {"id": "aura-arcas-en", "name": "Arcas (English)"},
            {"id": "aura-perseus-en", "name": "Perseus (English)"},
            {"id": "aura-angus-en", "name": "Angus (English)"},
            {"id": "aura-orpheus-en", "name": "Orpheus (English)"},
            {"id": "aura-helios-en", "name": "Helios (English)"},
            {"id": "aura-zeus-en", "name": "Zeus (English)"},
        ]
    except Exception as e:
        log.error(f"Error fetching Deepgram voices: {e}")
        return []


def fetch_voices_from_playht(api_key: str, user_id: str) -> list[dict]:
    """
    Fetch voices from PlayHT (Play.ht).
    
    PlayHT provides an API to list available voices.
    """
    try:
        import requests
        
        response = requests.get(
            "https://api.play.ht/api/v2/voices",
            headers={
                "Authorization": f"Bearer {api_key}",
                "X-User-ID": user_id
            }
        )
        response.raise_for_status()
        
        data = response.json()
        voices = []
        
        for voice in data:
            name = voice.get('name', voice.get('id', 'Unknown'))
            language = voice.get('language', '')
            gender = voice.get('gender', '')
            
            display_name = name
            if language or gender:
                metadata = ", ".join(filter(None, [language, gender]))
                display_name = f"{name} ({metadata})"
            
            voices.append({
                "id": voice.get('id', voice.get('voice_engine', name)),
                "name": display_name
            })
        
        return voices
    except Exception as e:
        log.error(f"Error fetching PlayHT voices: {e}")
        return []


def fetch_voices_from_azure_cognitive(subscription_key: str, region: str) -> list[dict]:
    """
    Fetch voices from Azure Cognitive Services TTS (not Azure OpenAI).
    
    Azure provides a comprehensive voices list API.
    """
    try:
        import requests
        
        response = requests.get(
            f"https://{region}.tts.speech.microsoft.com/cognitiveservices/voices/list",
            headers={
                "Ocp-Apim-Subscription-Key": subscription_key
            }
        )
        response.raise_for_status()
        
        data = response.json()
        voices = []
        
        for voice in data:
            name = voice.get('ShortName', voice.get('Name', 'Unknown'))
            locale = voice.get('Locale', '')
            gender = voice.get('Gender', '')
            
            display_name = name
            if locale or gender:
                metadata = ", ".join(filter(None, [locale, gender]))
                display_name = f"{name} ({metadata})"
            
            voices.append({
                "id": voice.get('ShortName', name),
                "name": display_name
            })
        
        return voices
    except Exception as e:
        log.error(f"Error fetching Azure Cognitive Services voices: {e}")
        return []


def fetch_voices_from_ibm_watson(api_key: str, service_url: str) -> list[dict]:
    """
    Fetch voices from IBM Watson TTS.
    
    IBM Watson provides a voices API.
    """
    try:
        import requests
        from requests.auth import HTTPBasicAuth
        
        # Ensure service_url has protocol
        service_url = _ensure_protocol(service_url)
        
        # IBM Watson uses apikey as username
        auth = HTTPBasicAuth('apikey', api_key)
        
        response = requests.get(
            f"{service_url}/v1/voices",
            auth=auth
        )
        response.raise_for_status()
        
        data = response.json()
        voices = []
        
        for voice in data.get('voices', []):
            name = voice.get('name', 'Unknown')
            language = voice.get('language', '')
            gender = voice.get('gender', '')
            
            display_name = name
            if language or gender:
                metadata = ", ".join(filter(None, [language, gender]))
                display_name = f"{name} ({metadata})"
            
            voices.append({
                "id": voice.get('name', name),
                "name": display_name
            })
        
        return voices
    except Exception as e:
        log.error(f"Error fetching IBM Watson voices: {e}")
        return []


def fetch_tts_voices(config_data: dict) -> list[dict]:
    """Return available voices for the TTS configuration described by *config_data*.

    Strategy
    --------
    LiteLLM-managed providers (OpenAI, Azure OpenAI, Google/Vertex AI) share a
    fixed set of OpenAI-compatible voice IDs.  LiteLLM translates those IDs to
    the underlying provider's native voices at inference time, so no live API
    call is needed — the list is returned directly from constants.

    All other vendors (ElevenLabs, AWS Polly, Deepgram, PlayHT, IBM Watson,
    Azure Cognitive Services TTS) expose provider-specific catalogues that must
    be fetched from their respective APIs.

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
                log.warning("ai_credentials present but no configuration_type: keys=%s", list(ai_creds.keys()))

    if not config_type:
        log.warning(
            "Could not determine TTS provider type.  "
            "config_data keys=%s  data keys=%s",
            list(config_data.keys()), list(data.keys()),
        )
        return []

    t = config_type.replace('_', '')  # normalised type string for matching

    try:
        # ------------------------------------------------------------------
        # LiteLLM-managed providers — return fixed voice list, no API call
        # ------------------------------------------------------------------

        # OpenAI (plain or via LiteLLM proxy); Azure OpenAI is caught below
        if t == 'openai' or ('openai' in t and 'azure' not in t):
            log.info("LiteLLM/OpenAI TTS: returning fixed voice list for model '%s'", model)
            return fetch_voices_from_openai(model)

        # Azure OpenAI (config_type contains both 'azure' and 'openai', or is
        # 'azureopenai' / 'azure' on its own)
        if 'azure' in t and ('openai' in t or t in ('azure', 'azureopenai')):
            log.info("LiteLLM/Azure OpenAI TTS: returning fixed voice list for model '%s'", model)
            return fetch_voices_from_azure(model)

        # Google Cloud TTS / Vertex AI
        if t == 'google' or 'vertex' in t or 'gcp' in t or 'google' in t:
            log.info("LiteLLM/Google TTS: returning fixed voice list for model '%s'", model)
            return fetch_voices_from_google_cloud(model)

        # ------------------------------------------------------------------
        # Vendor-specific providers — fetch voices from their APIs
        # ------------------------------------------------------------------

        # Azure Cognitive Services TTS (distinct from Azure OpenAI)
        if t in ('azuretts', 'azurecognitive'):
            return fetch_voices_from_azure_cognitive(
                subscription_key=data.get('subscription_key', data.get('api_key', '')),
                region=data.get('region', 'eastus'),
            )

        # AWS Polly
        if t == 'aws' or 'polly' in t:
            return fetch_voices_from_aws_polly(
                access_key=data.get('aws_access_key_id', ''),
                secret_key=data.get('aws_secret_access_key', ''),
                region=data.get('region', 'us-east-1'),
            )

        # ElevenLabs
        if 'elevenlabs' in t or 'eleven' in t:
            return fetch_voices_from_elevenlabs(api_key=data.get('api_key', ''))

        # Deepgram Aura
        if 'deepgram' in t:
            return fetch_voices_from_deepgram(api_key=data.get('api_key', ''))

        # PlayHT / Play.ht
        if 'playht' in t or 'playai' in t:
            return fetch_voices_from_playht(
                api_key=data.get('api_key', ''),
                user_id=data.get('user_id', ''),
            )

        # IBM Watson TTS
        if 'ibm' in t or 'watson' in t:
            return fetch_voices_from_ibm_watson(
                api_key=data.get('api_key', ''),
                service_url=data.get('service_url', 'https://api.us-south.text-to-speech.watson.cloud.ibm.com'),
            )

        log.warning("Unknown TTS provider type: %s", config_type)
        return []

    except Exception as e:
        log.error("Error fetching TTS voices for %s: %s", config_type, e)
        return []
