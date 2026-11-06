from .common_utils import get_public_project_id
from .folder_access import folder_exclusion_clause
from .llm_model_identity import resolve_identity
from .local_tools import db, VaultClient
from .models.configuration import Configuration
from .models.pd.llm_model import LlmModelList, EmbeddingModelList, VectorStorageModelList, ImageGenerationModelList, ASRModelList, TTSModelList
from .utils import get_configuration_llm_models_with_limits_query, get_embedding_model_query, get_vector_storage_query, get_image_generation_model_query, get_asr_model_query, get_tts_model_query


from pylon.core.tools import log


class ConfigurationModelHandler:
    """Base class for handling configuration models"""

    def __init__(self, project_id: int, public_project_id: int):
        self.project_id = project_id
        self.public_project_id = public_project_id
        self.display_name_key = 'name'

    def get_query_method(self):
        """Override in subclasses to return the appropriate query method"""
        raise NotImplementedError

    def get_model_class(self):
        """Override in subclasses to return the appropriate model class"""
        raise NotImplementedError

    def get_private_filters(self):
        """Override in subclasses if special filters are needed"""
        clause = folder_exclusion_clause(self.project_id, Configuration.id)
        return [] if clause is None else [clause]

    def get_public_filters(self):
        """Override in subclasses if special filters are needed"""
        return [Configuration.shared == True]

    def fetch_configurations(self, session, project_id, filters, section):
        """Fetch configurations using the appropriate query method"""
        query_method = self.get_query_method()
        return query_method(session, project_id, filters, section)

    def validate_and_convert_model(self, config):
        """Convert configuration to model data"""
        model_class = self.get_model_class()
        return model_class.model_validate(config).model_dump(mode='json')


class LLMModelHandler(ConfigurationModelHandler):
    """Handler for LLM models"""

    def get_query_method(self):
        return get_configuration_llm_models_with_limits_query

    def get_model_class(self):
        return LlmModelList

    def fetch_configurations(self, session, project_id, filters, section):
        return self.get_query_method()(session, project_id, filters, section).all()

    def validate_and_convert_model(self, config):
        model_data = super().validate_and_convert_model(config)
        model_data['identity'] = resolve_identity(
            model_data['name'], api_protocol=model_data.get('api_protocol'),
            explicit=getattr(config, 'canonical_model', None))
        return model_data


class EmbeddingModelHandler(ConfigurationModelHandler):
    """Handler for embedding models"""

    def get_query_method(self):
        return get_embedding_model_query

    def get_model_class(self):
        return EmbeddingModelList


class VectorStorageModelHandler(ConfigurationModelHandler):
    """Handler for vector storage models"""

    def get_query_method(self):
        return get_vector_storage_query

    def get_model_class(self):
        return VectorStorageModelList


class ImageGenerationModelHandler(ConfigurationModelHandler):
    """Handler for image generation models"""

    def get_query_method(self):
        return get_image_generation_model_query

    def get_model_class(self):
        return ImageGenerationModelList


class ASRModelHandler(ConfigurationModelHandler):
    """Handler for ASR (Speech Recognition) models"""

    def get_query_method(self):
        return get_asr_model_query

    def get_model_class(self):
        return ASRModelList


class TTSModelHandler(ConfigurationModelHandler):
    """Handler for TTS (Text-to-Speech) models"""

    def get_query_method(self):
        return get_tts_model_query

    def get_model_class(self):
        return TTSModelList


class ModelConfigurationService:
    """Service class to orchestrate model configuration retrieval"""

    HANDLERS = {
        'llm': LLMModelHandler,
        'embedding': EmbeddingModelHandler,
        'vectorstorage': VectorStorageModelHandler,
        'image_generation': ImageGenerationModelHandler,
        'asr': ASRModelHandler,
        'tts': TTSModelHandler,
    }

    def __init__(self, project_id: int):
        self.project_id = project_id
        self.public_project_id = get_public_project_id()
        self.display_name_key = 'name'

    def get_handler(self, section: str) -> ConfigurationModelHandler:
        """Get the appropriate handler for the section"""
        if section not in self.HANDLERS:
            raise ValueError(f"Unknown section: {section}")

        handler_class = self.HANDLERS[section]
        return handler_class(self.project_id, self.public_project_id)

    def get_default_model_info(self, section: str):
        """Get default model information from vault secrets"""
        vault_client = VaultClient.from_project(self.project_id)
        secrets = vault_client.get_secrets()
        secret_key = f'default_{section}_model_name'
        secret_key_project = f'default_{section}_model_project_id'
        default_model_name_secret = secrets.get(secret_key)
        default_model_project_id = secrets.get(secret_key_project)

        if not default_model_name_secret or not default_model_project_id:
            public_vault_client = VaultClient.from_project(self.public_project_id)
            all_secrets = public_vault_client.get_all_secrets()
            if not default_model_name_secret:
                default_model_name_secret = all_secrets.get(secret_key)
            if not default_model_project_id:
                default_model_project_id = all_secrets.get(secret_key_project)

        return default_model_name_secret, default_model_project_id

    def fetch_private_configurations(self, section: str, handler: ConfigurationModelHandler):
        """Fetch private configurations for the project"""
        distinct_items = {}

        with db.get_session(self.project_id) as session:
            private_filters = handler.get_private_filters()
            private_configurations = handler.fetch_configurations(
                session, self.project_id, private_filters, section
            )

            # Add private configurations to distinct items dict
            for config in private_configurations:
                model_data = handler.validate_and_convert_model(config)
                key = (self.project_id, model_data[self.display_name_key])
                distinct_items[key] = model_data

        return distinct_items

    def fetch_shared_configurations(self, section: str, handler: ConfigurationModelHandler, distinct_items: dict):
        """Fetch shared configurations from public project if include_shared is True"""
        if self.project_id != self.public_project_id:
            with db.get_session(self.public_project_id) as public_session:
                public_filters = handler.get_public_filters()
                public_configurations = handler.fetch_configurations(
                    public_session, self.public_project_id, public_filters, section
                )

                # Add public configurations to distinct items dict
                for config in public_configurations:
                    model_data = handler.validate_and_convert_model(config)
                    key = (self.public_project_id, model_data[self.display_name_key])
                    distinct_items[key] = model_data

        return distinct_items

    def determine_default_model(self, default_model_name_secret, default_model_project_id_secret, distinct_items: dict):
        """Determine the default model based on available models and secrets, using both name and project_id"""
        available_models = [(proj_id, model_name) for (proj_id, model_name) in distinct_items.keys()]
        default_model_name = None
        default_model_project_id = None

        # Use both name and project_id from secrets to find the default
        if default_model_name_secret and default_model_project_id_secret:
            for (proj_id, model_name) in available_models:
                if model_name == default_model_name_secret and str(proj_id) == str(default_model_project_id_secret):
                    default_model_name = model_name
                    default_model_project_id = proj_id
                    break
        # Fallback: if not found, pick the first available
        if default_model_name is None and available_models:
            default_model_name = available_models[0][1]
            default_model_project_id = available_models[0][0]

        return default_model_name, default_model_project_id

    def resolve_llm_default(self, section: str, distinct_items: dict, pointers: 'DefaultModelPointers', fallback=None):
        """Resolve an LLM default: own pointer, then Public's, then the fallback, then the first in display order."""
        predicate = TIER_PREDICATES.get(section, accepts_any_model)
        for read_pointer in (pointers.own_pointer, pointers.public_pointer):
            found = find_pointed_model(read_pointer(section), distinct_items, predicate)
            if found:
                return found
        if fallback and fallback[0]:
            return fallback
        for key in keys_in_display_order(distinct_items):
            if predicate(distinct_items[key]):
                return key[1], key[0]
        return None, None

    def prepare_response(self, distinct_items: dict, default_model_name: str, default_model_project_id: int):
        """Prepare the final response with default flags"""
        items_with_default_flag = []
        for key in keys_in_display_order(distinct_items):
            proj_id, model_name = key
            model_data = distinct_items[key]
            model_data['default'] = (
                model_name == default_model_name and proj_id == default_model_project_id
            )
            items_with_default_flag.append(model_data)

        return {
            "total": len(distinct_items),
            "items": items_with_default_flag,
            "default_model_name": default_model_name,
            "default_model_project_id": default_model_project_id,
        }

    def get_available_models(self, section: str, include_shared: bool = False) -> dict:
        try:
            handler = self.get_handler(section)
        except ValueError:
            log.error(f"Error getting available models for section {section}")
            return {}

        # Fetch private configurations
        distinct_items = self.fetch_private_configurations(section, handler)

        # Fetch shared configurations if requested
        if include_shared:
            distinct_items = self.fetch_shared_configurations(section, handler, distinct_items)

        return distinct_items

    def get_models(self, section: str, include_shared: bool = False):
        """Main method to get models for a given section"""

        distinct_items = self.get_available_models(section, include_shared)

        if section != 'llm':
            default_model_name, default_model_project_id = self.determine_default_model(
                *self.get_default_model_info(section), distinct_items
            )
            return self.prepare_response(distinct_items, default_model_name, default_model_project_id), 200

        pointers = DefaultModelPointers(self.project_id, self.public_project_id)
        low_tier = self.resolve_llm_default('llm_low_tier', distinct_items, pointers)
        high_tier = self.resolve_llm_default('llm_high_tier', distinct_items, pointers)
        default_model_name, default_model_project_id = self.resolve_llm_default(
            section, distinct_items, pointers, fallback=low_tier
        )

        response = self.prepare_response(distinct_items, default_model_name, default_model_project_id)
        response.update({
            'low_tier_default_model_name': low_tier[0] or '',
            'low_tier_default_model_project_id': low_tier[1] or '',
            'high_tier_default_model_name': high_tier[0] or '',
            'high_tier_default_model_project_id': high_tier[1] or '',
        })
        return response, 200


TIER_PREDICATES = {
    'llm_low_tier': lambda model_data: model_data.get('low_tier') is True,
    'llm_high_tier': lambda model_data: model_data.get('high_tier') is True,
}


def accepts_any_model(model_data: dict) -> bool:
    """Default sections take any available model."""
    return True


def keys_in_display_order(distinct_items: dict) -> list:
    """Shared first, then display name, with project and name breaking ties so the order never varies."""
    def display_order_key(item_key):
        proj_id, model_name = item_key
        model_data = distinct_items[item_key]
        display_name = (model_data.get('display_name') or model_data.get('name') or model_name or '').lower()
        return not model_data.get('shared'), display_name, proj_id, model_name
    return sorted(distinct_items, key=display_order_key)


def find_pointed_model(pointer: tuple, distinct_items: dict, predicate):
    """Return (name, project_id) of the available model a vault pointer names, if it passes the predicate."""
    pointer_name, pointer_project_id = pointer
    if not pointer_name or not pointer_project_id:
        return None
    for (proj_id, model_name), model_data in distinct_items.items():
        if model_name == pointer_name and str(proj_id) == str(pointer_project_id) and predicate(model_data):
            return model_name, proj_id
    return None


class DefaultModelPointers:
    """Default-model vault pointers, reading each vault at most once and Public's only when needed."""

    def __init__(self, project_id: int, public_project_id: int):
        self.project_id = project_id
        self.public_project_id = public_project_id
        self._own_secrets = None
        self._public_secrets = None

    def own_pointer(self, section: str) -> tuple:
        """The project's own (name, project_id) pointer for a section."""
        if self._own_secrets is None:
            self._own_secrets = VaultClient.from_project(self.project_id).get_secrets()
        return pointer_from_secrets(self._own_secrets, section)

    def public_pointer(self, section: str) -> tuple:
        """Public's (name, project_id) pointer for a section, inherited by every project."""
        if self._public_secrets is None:
            self._public_secrets = VaultClient.from_project(self.public_project_id).get_all_secrets()
        return pointer_from_secrets(self._public_secrets, section)


def pointer_from_secrets(secrets: dict, section: str) -> tuple:
    """Read a section's default pointer as one (name, project_id) pair."""
    return secrets.get(f'default_{section}_model_name'), secrets.get(f'default_{section}_model_project_id')
