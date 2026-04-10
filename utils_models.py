from .common_utils import get_public_project_id
from .local_tools import db, VaultClient
from .models.configuration import Configuration
from .models.pd.llm_model import LlmModelList, EmbeddingModelList, VectorStorageModelList, ImageGenerationModelList
from .utils import get_configuration_llm_models_with_limits_query, get_embedding_model_query, get_vector_storage_query, get_image_generation_model_query


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
        return []

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


class ModelConfigurationService:
    """Service class to orchestrate model configuration retrieval"""

    HANDLERS = {
        'llm': LLMModelHandler,
        'embedding': EmbeddingModelHandler,
        'vectorstorage': VectorStorageModelHandler,
        'image_generation': ImageGenerationModelHandler,
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

    def determine_explicit_default_model(self, default_model_name_secret, default_model_project_id_secret, distinct_items: dict, predicate=None):
        """Determine a default model only if explicitly set (no fallback).

        Optionally apply a predicate(model_data) filter.
        """
        if not default_model_name_secret or not default_model_project_id_secret:
            return None, None

        for (proj_id, model_name), model_data in distinct_items.items():
            if model_name != default_model_name_secret:
                continue
            if str(proj_id) != str(default_model_project_id_secret):
                continue
            if predicate and not predicate(model_data):
                return None, None
            return model_name, proj_id

        return None, None

    def prepare_response(self, distinct_items: dict, default_model_name: str, default_model_project_id: int):
        """Prepare the final response with default flags"""
        items_with_default_flag = []
        for (proj_id, model_name), model_data in distinct_items.items():
            model_data['default'] = (
                model_name == default_model_name and proj_id == default_model_project_id
            )
            items_with_default_flag.append(model_data)

        # Sort by shared status and display_name (fallback to name if display_name not present)
        if items_with_default_flag:
            items_with_default_flag.sort(key=lambda x: (
                not x['shared'],
                (x.get('display_name') or x.get('name', '')).lower()
            ))

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

        # Get default model information
        default_model_name_secret, default_model_project_id_secret = self.get_default_model_info(section)

        # Determine the actual default model
        default_model_name, default_model_project_id = self.determine_default_model(
            default_model_name_secret, default_model_project_id_secret, distinct_items
        )

        # Prepare response
        response = self.prepare_response(distinct_items, default_model_name, default_model_project_id)

        # Extend LLM section response with tier defaults (stored under their own vault keys)
        if section == 'llm':
            low_tier_name_secret, low_tier_project_id_secret = self.get_default_model_info('llm_low_tier')
            high_tier_name_secret, high_tier_project_id_secret = self.get_default_model_info('llm_high_tier')

            low_tier_name, low_tier_project_id = self.determine_explicit_default_model(
                low_tier_name_secret,
                low_tier_project_id_secret,
                distinct_items,
                predicate=lambda model_data: model_data.get('low_tier') is True,
            )
            high_tier_name, high_tier_project_id = self.determine_explicit_default_model(
                high_tier_name_secret,
                high_tier_project_id_secret,
                distinct_items,
                predicate=lambda model_data: model_data.get('high_tier') is True,
            )

            response.update({
                'low_tier_default_model_name': low_tier_name or '',
                'low_tier_default_model_project_id': low_tier_project_id or '',
                'high_tier_default_model_name': high_tier_name or '',
                'high_tier_default_model_project_id': high_tier_project_id or '',
            })

        return response, 200
