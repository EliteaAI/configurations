"""
Provider-specific voice fetchers for TTS integrations.

Each function accepts the flattened configuration data dict and returns a list of
``{'id': ..., 'name': ...}`` dicts, or ``[]`` on error.
"""
import os
from pylon.core.tools import log

# ---------------------------------------------------------------------------
# External provider API base URLs — override via environment variables for
# on-premise or private-cloud deployments.
# ---------------------------------------------------------------------------
_ELEVENLABS_API_BASE = os.environ.get('ELEVENLABS_API_BASE_URL', 'https://api.elevenlabs.io')
_DEEPGRAM_API_BASE   = os.environ.get('DEEPGRAM_API_BASE_URL',   'https://api.deepgram.com')
_PLAYHT_API_BASE     = os.environ.get('PLAYHT_API_BASE_URL',     'https://api.play.ht')
_IBM_WATSON_TTS_DEFAULT_URL = os.environ.get(
    'IBM_WATSON_TTS_DEFAULT_URL',
    'https://api.us-south.text-to-speech.watson.cloud.ibm.com',
)


def _ensure_protocol(url: str) -> str:
    """Ensure URL has http:// or https:// protocol."""
    if not url:
        return url
    url = url.strip()
    if not url.startswith(('http://', 'https://')):
        return f'https://{url}'
    return url


def fetch_voices_from_aws_polly(data: dict) -> list[dict]:
    """Fetch voices from AWS Polly via the describe_voices() API."""
    try:
        import boto3

        client = boto3.client(
            'polly',
            aws_access_key_id=data.get('aws_access_key_id', ''),
            aws_secret_access_key=data.get('aws_secret_access_key', ''),
            region_name=data.get('region', 'us-east-1'),
        )

        response = client.describe_voices()

        return [
            {
                "id": v['Id'],
                "name": f"{v['Name']} ({v['LanguageName']}, {v['Gender']})",
            }
            for v in response.get('Voices', [])
        ]
    except ImportError:
        log.warning("boto3 (AWS SDK) not installed")
        return []
    except Exception as e:
        log.error("Error fetching AWS Polly voices: %s", e)
        return []


def fetch_voices_from_elevenlabs(data: dict) -> list[dict]:
    """Fetch voices from ElevenLabs via the /v1/voices API."""
    try:
        import requests

        response = requests.get(
            f"{_ELEVENLABS_API_BASE}/v1/voices",
            headers={"xi-api-key": data.get('api_key', '')},
        )
        response.raise_for_status()

        voices = []
        for voice in response.json().get('voices', []):
            category = voice.get('category', '')
            name = voice['name']
            if category:
                name = f"{name} ({category})"
            voices.append({"id": voice['voice_id'], "name": name})

        return voices
    except Exception as e:
        log.error("Error fetching ElevenLabs voices: %s", e)
        return []


def fetch_voices_from_deepgram(data: dict, static_voices: list[dict]) -> list[dict]:
    """Validate Deepgram API key and return the static Aura voice list.

    Deepgram does not have a dedicated voice-listing endpoint; the Aura voice
    catalogue is defined in ``data/tts_voices.json``.  We make a lightweight
    API call only to verify the credentials.
    """
    try:
        import requests

        response = requests.get(
            f"{_DEEPGRAM_API_BASE}/v1/projects",
            headers={"Authorization": f"Token {data.get('api_key', '')}"},
        )
        response.raise_for_status()

        return static_voices
    except Exception as e:
        log.error("Error fetching Deepgram voices: %s", e)
        return []


def fetch_voices_from_playht(data: dict) -> list[dict]:
    """Fetch voices from PlayHT via the /api/v2/voices API."""
    try:
        import requests

        response = requests.get(
            f"{_PLAYHT_API_BASE}/api/v2/voices",
            headers={
                "Authorization": f"Bearer {data.get('api_key', '')}",
                "X-User-ID": data.get('user_id', ''),
            },
        )
        response.raise_for_status()

        voices = []
        for voice in response.json():
            name     = voice.get('name', voice.get('id', 'Unknown'))
            language = voice.get('language', '')
            gender   = voice.get('gender', '')
            metadata = ", ".join(filter(None, [language, gender]))
            display  = f"{name} ({metadata})" if metadata else name
            voices.append({
                "id":   voice.get('id', voice.get('voice_engine', name)),
                "name": display,
            })

        return voices
    except Exception as e:
        log.error("Error fetching PlayHT voices: %s", e)
        return []


def fetch_voices_from_azure_cognitive(data: dict) -> list[dict]:
    """Fetch voices from Azure Cognitive Services TTS (distinct from Azure OpenAI)."""
    try:
        import requests

        region = data.get('region', 'eastus')
        response = requests.get(
            f"https://{region}.tts.speech.microsoft.com/cognitiveservices/voices/list",
            headers={
                "Ocp-Apim-Subscription-Key": data.get(
                    'subscription_key', data.get('api_key', '')
                ),
            },
        )
        response.raise_for_status()

        voices = []
        for voice in response.json():
            name     = voice.get('ShortName', voice.get('Name', 'Unknown'))
            locale   = voice.get('Locale', '')
            gender   = voice.get('Gender', '')
            metadata = ", ".join(filter(None, [locale, gender]))
            display  = f"{name} ({metadata})" if metadata else name
            voices.append({"id": voice.get('ShortName', name), "name": display})

        return voices
    except Exception as e:
        log.error("Error fetching Azure Cognitive Services voices: %s", e)
        return []


def fetch_voices_from_ibm_watson(data: dict) -> list[dict]:
    """Fetch voices from IBM Watson TTS via the /v1/voices API."""
    try:
        import requests
        from requests.auth import HTTPBasicAuth

        service_url = _ensure_protocol(
            data.get('service_url', _IBM_WATSON_TTS_DEFAULT_URL)
        )

        response = requests.get(
            f"{service_url}/v1/voices",
            auth=HTTPBasicAuth('apikey', data.get('api_key', '')),
        )
        response.raise_for_status()

        voices = []
        for voice in response.json().get('voices', []):
            name     = voice.get('name', 'Unknown')
            language = voice.get('language', '')
            gender   = voice.get('gender', '')
            metadata = ", ".join(filter(None, [language, gender]))
            display  = f"{name} ({metadata})" if metadata else name
            voices.append({"id": voice.get('name', name), "name": display})

        return voices
    except Exception as e:
        log.error("Error fetching IBM Watson voices: %s", e)
        return []
