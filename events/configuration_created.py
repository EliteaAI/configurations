from pylon.core.tools import web, log


class Event:
    @web.event('configuration_created')
    def configuration_created(self, context, event, payload: dict):
        configuration_type = payload['type']
        if configuration_type in ('pgvector', 'asr_model', 'tts_model'):
            update_payload = {'status_ok': True}
            
            # Auto-populate voices for TTS models by querying the provider
            if configuration_type == 'tts_model':
                existing_meta = payload.get('meta', {})
                
                # Only fetch voices if not already configured
                if not existing_meta.get('voices'):
                    try:
                        from ..models.pd.llm_model import TTSModel
                        from ..utils import expand_configuration
                        
                        # Get user_id from payload (set during configuration creation)
                        user_id = payload.get('author_id') or payload.get('user_id')
                        
                        # Build wrapped config dict (same shape as _fetch_voices_from_provider)
                        config_data = {
                            'type': configuration_type,
                            'data': dict(payload.get('data', {})),
                            'project_id': payload['project_id'],
                        }

                        # Expand in place to resolve ai_credentials references
                        expand_configuration(
                            payload=config_data,
                            current_project_id=payload['project_id'],
                            user_id=user_id,
                            unsecret=True
                        )

                        # Call check_connection to fetch voices from provider
                        result = TTSModel.check_connection(config_data)
                        
                        if isinstance(result, dict) and 'voices' in result:
                            voices = result['voices']
                            if voices:
                                updated_meta = {**existing_meta, 'voices': voices}
                                update_payload['meta'] = updated_meta
                                log.info(f"Auto-fetched {len(voices)} voices from TTS provider: {payload.get('data', {}).get('name', 'unknown')}")
                        elif isinstance(result, str):
                            # Error occurred - log but don't fail configuration creation
                            log.warning(f"Could not fetch TTS voices: {result}")
                    except Exception as e:
                        # Don't fail configuration creation if voice fetching fails
                        log.error(f"Error auto-fetching TTS voices: {e}")
            
            self.update_configuration_rpc(
                project_id=payload['project_id'],
                config_id=payload['id'],
                payload=update_payload
            )
