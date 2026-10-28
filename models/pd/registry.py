from traceback import format_exc
from typing import Any, Optional

from pydantic import BaseModel, field_validator, model_validator

from .environment_settings import EnvironmentSettings
from .auto_routing import AutoRoutingProjectSettings
from .web_search_settings import WebSearchProjectSettings
from .context_window_settings import ContextWindowProjectSettings
from .llm_model import LlmModel, EmbeddingModel, ImageGenerationModel, ASRModel, TTSModel
from .llm_model_connection import check_llm_model_connection
from .project_context import ProjectContext
from .project_chat_config import ProjectChatConfig
from .project_icon import ProjectIcon
from .project_skills import ProjectSkillsSettings
from .service_prompt import ServicePrompt
from ...local_tools import log


class ConfigTypeRegistryItem(BaseModel):
    type: str
    section: str
    model: Any = None  # This should be a Pydantic model class
    validation_func: Optional[str] = None  # Name of validation function to call via RPC if no model
    config_schema: dict[str, Any] | None = None  # Optional schema for the model
    check_connection_func: Optional[str] = None  # Name of check connection function to call via RPC if no model
    managed_by: str | None = None  # Owner plugin: the REST API refuses writes, the owner writes through RPC
    manage_url: str | None = None  # Where a client changes the owned records instead

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
                    elif 'tools' in result or 'warning' in result or 'success' not in result:
                        # Extended success response (e.g., tools discovery, non-blocking warning) - preserve the dict
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


_REGISTRY_VERSION = [0]


def registry_version() -> int:
    """A counter that changes whenever ``CONFIG_TYPE_REGISTRY`` is changed through the functions below.

    Caches of schema-derived data (the secret field paths of a type) key on it, so a re-registered or removed type
    is never answered from an old schema.
    """
    return _REGISTRY_VERSION[0]


def _registry_changed() -> None:
    _REGISTRY_VERSION[0] += 1


def clear_config_type_registry():
    CONFIG_TYPE_REGISTRY.clear()
    _registry_changed()


def register_config_type(type_name: str, section: str, model=None, validation_func=None, config_schema=None,
                         check_connection_func=None, replace=False, managed_by=None, manage_url=None):
    """
    Register a new configuration type in CONFIG_TYPE_REGISTRY.
    User may provide either a model or a validation_func name (str).
    A managed_by owner makes the REST API refuse writes of the type; see managed_configuration_error.
    Raises ValueError if type_name already exists and replacement was not requested,
    or if a replacement would drop or change the owner of an owned type.

    Replacement is transactional: the existing entry remains registered when
    validation of its successor fails.
    """
    if type_name in CONFIG_TYPE_REGISTRY and not replace:
        raise ValueError(f'Configuration type {type_name} already exists')
    existing = CONFIG_TYPE_REGISTRY.get(type_name)
    if existing and existing.managed_by and existing.managed_by != managed_by:
        raise ValueError(f'Configuration type {type_name} is managed by {existing.managed_by}')
    try:
        item = ConfigTypeRegistryItem.model_construct(
            type=type_name,
            section=section,
            model=model,
            validation_func=validation_func,
            config_schema=config_schema,
            check_connection_func=check_connection_func,
            managed_by=managed_by,
            manage_url=manage_url,
        )
        item = ConfigTypeRegistryItem.model_validate(item.model_dump(exclude_unset=True))
        log.info(f'Registering config: {item}')
        CONFIG_TYPE_REGISTRY[type_name] = item
        _registry_changed()
    except:
        log.error(format_exc())


def unregister_config_type(type_name: str, owner: str | None = None) -> bool:
    """Remove a dynamic configuration type without touching saved records.

    An owned type is refused when owner names another plugin; a missing owner passes, as in assert_write_allowed.
    """
    existing = CONFIG_TYPE_REGISTRY.get(type_name)
    if existing and existing.managed_by and owner is not None and existing.managed_by != owner:
        raise ValueError(f'Configuration type {type_name} is managed by {existing.managed_by}')
    removed = CONFIG_TYPE_REGISTRY.pop(type_name, None) is not None
    if removed:
        _registry_changed()
    return removed


def manage_url_for(entry: ConfigTypeRegistryItem, project_id: int | None) -> str | None:
    """The owner's URL with its placeholders filled; None when the project is unknown or the template does not fit."""
    if not (entry.manage_url and project_id):
        return None
    try:
        return entry.manage_url.format_map({'mode': 'default', 'project_id': project_id})
    except (KeyError, IndexError, ValueError):
        return None


def owned_entry(type_name, title) -> ConfigTypeRegistryItem | None:
    """The owned registry entry that claims this type, or this title (an owned type reserves its own name as a title).

    A title matches ignoring case and surrounding spaces, so a near copy of the name is reserved too.
    """
    for key in (type_name, title.strip().lower() if isinstance(title, str) else None):
        entry = CONFIG_TYPE_REGISTRY.get(key) if isinstance(key, str) else None
        if entry and entry.managed_by:
            return entry
    return None


def managed_configuration_error(type_name, title: str | None = None, project_id: int | None = None) -> dict | None:
    """The 403 body for a REST write of an owned type or an owned title; None when the REST API may write it."""
    entry = owned_entry(type_name, title)
    if not entry:
        return None
    manage_url = manage_url_for(entry, project_id)
    where = manage_url or f'the {entry.managed_by} API'
    return {
        'code': 'managed_configuration',
        'error': f'{entry.type} is managed by {entry.managed_by}; change it through {where}',
        'manage_url': manage_url,
    }


LlmModel.check_connection = staticmethod(check_llm_model_connection)
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
    type_name='asr_model',
    section='asr',
    model=ASRModel
)

register_config_type(
    type_name='tts_model',
    section='tts',
    model=TTSModel
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

register_config_type(
    type_name='project_context',
    section='project_settings',
    model=ProjectContext,
)

register_config_type(
    type_name='project_icon',
    section='project_settings',
    model=ProjectIcon,
)

register_config_type(
    type_name='auto_routing',
    section='project_settings',
    model=AutoRoutingProjectSettings,
)

register_config_type(
    type_name='context_window_settings',
    section='project_settings',
    model=ContextWindowProjectSettings,
)

register_config_type(
    type_name='project_chat_config',
    section='project_settings',
    model=ProjectChatConfig,
)

register_config_type(
    type_name='project_skills',
    section='project_settings',
    model=ProjectSkillsSettings,
)

register_config_type(
    type_name='web_search_settings',
    section='project_settings',
    model=WebSearchProjectSettings,
)
