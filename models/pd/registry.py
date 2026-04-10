from traceback import format_exc
from typing import Any, Optional

from pydantic import BaseModel, field_validator, model_validator

from .environment_settings import EnvironmentSettings
from .llm_model import LlmModel, EmbeddingModel, ImageGenerationModel
from .service_prompt import ServicePrompt
from ...local_tools import log


class ConfigTypeRegistryItem(BaseModel):
    type: str
    section: str
    model: Any = None  # This should be a Pydantic model class
    validation_func: Optional[str] = None  # Name of validation function to call via RPC if no model
    config_schema: dict[str, Any] | None = None  # Optional schema for the model
    check_connection_func: Optional[str] = None  # Name of check connection function to call via RPC if no model

    @model_validator(mode='after')
    def check_model_or_func(self):
        if not (self.model or self.validation_func):
            raise ValueError('Either model or validation_func must be provided')
        return self

    @field_validator('config_schema', mode='after', check_fields=False)
    def check_schema_is_always_set(cls, v, info):
        data = info.data
        if data.get('validation_func') and not v:
            raise ValueError('config_schema must be provided if validation_func is defined')
        if not v and data.get('model'):
            return data.get('model').schema()  # Use model's schema if available
        return v

    def check_connection(self, expanded_data: dict) -> str | dict | None:
        '''
        Returns:
            - None if OK (simple success)
            - dict with additional data on success (e.g., {'tools': [...]} for MCP discovery)
            - str containing error message if not OK
        '''
        result = None
        if not self.check_connection_func:
            if self.model and hasattr(self.model, "check_connection") and callable(getattr(self.model, "check_connection")):
                try:
                    result = self.model.check_connection(expanded_data)
                except Exception as e:
                    log.error(f'Error checking connection for {self.type}: {e}')
                    result = str(e)
                if result is not None and isinstance(result, dict):
                    if result.get('success') is False:
                        # Error case - extract message
                        result = result.get("message")
                    elif 'tools' in result or 'success' not in result:
                        # Extended success response (e.g., tools discovery) - preserve the dict
                        pass  # Keep result as-is
                    else:
                        # Simple success
                        result = None
            else:
                result = f"Checking connection is not supported yet for configuration type {self.type}"
                # TODO: or PASS with logging ?
                # log.error(result)
                # result = None
        else:
            from ...local_tools import rpc_manager

            rpc_validator = getattr(rpc_manager.timeout(5), self.check_connection_func)
            try:
                result = rpc_validator(type_=self.type, settings=expanded_data)
            except Exception as e:
                log.error(f'Error checking connection for {self.type}: {e}')
                result = str(e)

        if result is not None:
            return result
        return


CONFIG_TYPE_REGISTRY: dict[str, ConfigTypeRegistryItem] = {
    # "openai": ConfigTypeRegistryItem.model_validate({"type": "openai", "section": "llm", "model": OpenAIConfig}),
    # "llm_model": ConfigTypeRegistryItem.model_validate({"type": "llm_model", "section": "llm", "model": LlmModel}),
    # "azure_openai": ConfigTypeRegistryItem.model_validate({"type": "azure_openai", "section": "llm", "model": AzureOpenAIConfig}),
    # "s3": ConfigTypeRegistryItem.model_validate({"type": "s3", "section": "storage", "model": S3Config}),
    # "pgvector": ConfigTypeRegistryItem.model_validate({"type": "pgvector", "section": "vector", "model": PgvectorConfig}),
    # "vertex_ai": ConfigTypeRegistryItem.model_validate({"type": "vertex_ai", "section": "llm", "model": VertexAIConfig}),
    # Example with validation_func only:
    # "custom_type": {"type": "custom_type", "section": "custom", "validation_func": "custom_validate_func"},
}


def clear_config_type_registry():
    CONFIG_TYPE_REGISTRY.clear()


def register_config_type(type_name: str, section: str, model=None, validation_func=None, config_schema=None,
                         check_connection_func=None):
    """
    Register a new configuration type in CONFIG_TYPE_REGISTRY.
    User may provide either a model or a validation_func name (str).
    Raises ValueError if type_name already exists or neither is provided.
    """
    if type_name in CONFIG_TYPE_REGISTRY:
        raise ValueError(f'Configuration type {type_name} already exists')
    try:
        item = ConfigTypeRegistryItem.model_construct(
            type=type_name,
            section=section,
            model=model,
            validation_func=validation_func,
            config_schema=config_schema,
            check_connection_func=check_connection_func
        )
        item = ConfigTypeRegistryItem.model_validate(item.model_dump(exclude_unset=True))
        log.info(f'Registering config: {item}')
        CONFIG_TYPE_REGISTRY[type_name] = item
    except:
        log.error(format_exc())


register_config_type(
    type_name='llm_model',
    section='llm',
    model=LlmModel
)

register_config_type(
    type_name='embedding_model',
    section='embedding',
    model=EmbeddingModel
)

register_config_type(
    type_name='image_generation_model',
    section='image_generation',
    model=ImageGenerationModel
)

register_config_type(
    type_name='service_prompt',
    section='service_prompts',
    model=ServicePrompt,
)

register_config_type(
    type_name='environment_settings',
    section='environment_settings',
    model=EnvironmentSettings,
)