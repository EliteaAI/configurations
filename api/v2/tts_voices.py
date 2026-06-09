from flask import request

from ...local_tools import APIBase, db, register_openapi
from ...models.configuration import Configuration
from ...models.pd.llm_model import TTSModel
from ...utils import expand_configuration
from ...local_tools import VaultClient

from tools import auth
from pylon.core.tools import log


def _get_voices_from_configuration(project_id: int, model_name: str) -> list | None:
    """
    Retrieve voices from TTS model configuration's meta field.
    Returns None if not found or voices not configured.
    """
    if not model_name:
        return None
    
    with db.with_project_schema_session(project_id) as session:
        config = session.query(Configuration).filter(
            Configuration.elitea_title == model_name,
            Configuration.section == 'tts'
        ).first()
        
        if config and config.meta:
            voices = config.meta.get('voices')
            if voices and isinstance(voices, list):
                return voices
    
    return None


def _fetch_voices_from_provider(project_id: int, model_name: str) -> list | None:
    """
    Fetch voices directly from the TTS provider API.
    Returns None if fetching fails or provider doesn't support dynamic voices.
    """
    try:
        with db.with_project_schema_session(project_id) as session:
            config = session.query(Configuration).filter(
                Configuration.elitea_title == model_name,
                Configuration.section == 'tts'
            ).first()
            
            if not config:
                return None
            
            # Build configuration dict for fetching
            config_data = {
                'type': config.type,
                'data': config.data,
                'project_id': project_id,
            }
            
            # Expand to get actual credentials (modifies config_data in place, returns None)
            expand_configuration(
                payload=config_data,
                current_project_id=project_id,
                user_id=auth.current_user().get('id'),
                unsecret=True
            )

            # Call check_connection to fetch voices
            result = TTSModel.check_connection(config_data)
            
            if isinstance(result, dict) and 'voices' in result:
                return result['voices']
                
    except Exception as e:
        log.error(f"Error fetching voices from provider: {e}")
    
    return None


def _resolve_voices(project_id: int, model_name: str, refresh: bool = False) -> list:
    """
    Resolve voices for a TTS model. Priority:
    1. Fetch from provider (if refresh=True)
    2. Voices from model configuration meta field
    3. Empty list (signals voices need to be configured)
    """
    # If refresh requested, try to fetch from provider first
    if refresh:
        provider_voices = _fetch_voices_from_provider(project_id, model_name)
        if provider_voices:
            return provider_voices
    
    # Try to get voices from configuration meta field
    config_voices = _get_voices_from_configuration(project_id, model_name)
    if config_voices:
        return config_voices

    # No cached voices — fetch from provider as automatic fallback
    provider_voices = _fetch_voices_from_provider(project_id, model_name)
    if provider_voices:
        return provider_voices

    log.warning(f"No voices found for TTS model '{model_name}' in project {project_id}.")
    return []


class API(APIBase):
    url_params = ['<int:project_id>']

    @register_openapi(
        name="List TTS Voices",
        description="Return available voices for a given TTS model. "
                    "Voices are loaded from: 1) Provider API (if refresh=true), "
                    "2) Model configuration meta.voices field, or 3) Empty list if not configured. "
                    "New configurations auto-fetch voices on creation. Use refresh=true to update.",
        parameters=[
            {"name": "model_name", "in": "query", "schema": {"type": "string"},
             "description": "TTS model name to resolve voices for"},
            {"name": "refresh", "in": "query", "schema": {"type": "boolean", "default": False},
             "description": "If true, fetch voices from provider API instead of using cached values"},
        ],
        available_to_users=True,
    )
    def get(self, project_id: int, **kwargs):
        model_name = request.args.get("model_name", "")
        refresh = request.args.get("refresh", "false").lower() == "true"
        voices = _resolve_voices(project_id, model_name, refresh)
        return {"voices": voices, "model_name": model_name}, 200
