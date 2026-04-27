from copy import deepcopy
from re import match
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, field_validator, UUID4, SecretStr, field_serializer, ValidationError, Field, model_validator
from pydantic_core.core_schema import SerializationInfo

from .registry import CONFIG_TYPE_REGISTRY, ConfigTypeRegistryItem
from ..configuration import Configuration
from ..enums import SourceTypes
from ...exceptions import ConfigurationError
from ...local_tools import rpc_manager, log


class ConfigurationCreateBase(BaseModel):
    """Model for creating a new configuration via API."""
    elitea_title: str = Field(
        title="ID",
        description="Unique identifier for the configuration (alphanumeric and underscores only)"
    )
    label: Optional[str] = Field(
        None,
        title="Display Name",
        description="Human-readable name of the configuration"
    )
    type: str = Field(
        description="Configuration type (e.g., 'ai_model', 'integration', 'service_prompt')"
    )
    shared: bool = Field(
        False,
        description="Whether the configuration is shared across projects"
    )
    data: dict[str, Any] = Field(
        description="Configuration-specific data payload (schema depends on type)"
    )

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "elitea_title": "gpt51",
                    "label": "GPT-5.1",
                    "type": "llm_model",
                    "shared": False,
                    "data": {
                        "name": "gpt-5.1",
                        "low_tier": False,
                        "high_tier": True,
                        "ai_credentials": {
                            "private": False, "elitea_title": "azure_creds_public"
                        },
                        "context_window": 400000,
                        "max_output_tokens": 128000,
                        "supports_reasoning": True
                    }
                },
                {
                    "elitea_title": "githubcreds",
                    "label": "Github Credentials",
                    "type": "github",
                    "shared": True,
                    "data": {
                        "app_id": "",
                        "base_url": "https://api.github.com",
                        "password": "",
                        "username": "",
                        "access_token": "",
                        "app_private_key": ""
                    }
                }
            ]
        }
    )


class ConfigurationCreate(BaseModel):
    @field_serializer('data')
    def convert_secret_strings(self, v: dict, info: SerializationInfo):
        context = info.context
        if context.get('unsecret'):
            for k in v.keys():
                if isinstance(v[k], SecretStr):
                    v[k] = v[k].get_secret_value()
        return v

    project_id: int
    elitea_title: str
    type: str
    label: Optional[str] = None
    shared: bool = False
    uuid: str | UUID4 | None = None
    meta: dict[str, Any] = {}
    source: SourceTypes = SourceTypes.user
    author_id: int = None
    data: dict[str, Any]

    @field_validator('elitea_title', mode='after')
    def validate_elitea_title(cls, v) -> str:
        """
        This method checks that the elitea_title does not exceed 128 characters, is not empty,
        and contains only alphanumeric characters and underscores. If any of these
        conditions are not met, a ValueError is raised with an appropriate message.
        """
        if len(v) > 128:
            raise ValueError('EliteA title must not exceed 128 characters')

        if not v:
            raise ValueError('EliteA title cannot be empty')

        if not match(r'^[a-zA-Z0-9_-]+$', v):
            raise ValueError(
                'EliteA title must contain only alphanumeric characters and underscores (no spaces or special symbols)'
            )

        return v.lower()

    @field_validator('type', mode='before')
    def check_registry(cls, v):
        entry = CONFIG_TYPE_REGISTRY.get(v)
        if not entry:
            raise ValueError(f"Unknown configuration type: {v}")
        return v

    @field_validator('data', mode='after')
    def validate_data(cls, v, info):
        try:
            config_type = info.data['type']
        except KeyError:
            raise ConfigurationError('type', f"Configuration type was not found")
        entry: ConfigTypeRegistryItem = CONFIG_TYPE_REGISTRY[config_type]

        if entry.model:
            # TODO: temp fix for pydantic v1
            if hasattr(entry.model, 'model_validate'):
                validated: BaseModel = entry.model.model_validate(v)
                return validated.model_dump(mode='python')
            else:
                validated = entry.model.parse_obj(v)
                return validated.dict()

        elif entry.validation_func:
            from ...utils import expand_configuration

            rpc_validator = getattr(rpc_manager.timeout(5), entry.validation_func)
            value = deepcopy(v)
            current_project_id = info.data['project_id']
            user_id = info.data['author_id']
            expand_configuration(value, current_project_id=current_project_id, user_id=user_id, unsecret=True)

            try:
                rpc_validator(value, type_=config_type)
            except ValidationError as e:
                log.error(f"Validation error for configuration type '{config_type}': {e}")
                raise ConfigurationError('type', f"Validation error for configuration type '{config_type}'")
            return v

        raise ValueError("No model or validation_func defined for this configuration type")

    @model_validator(mode='after')
    def validate_ai_credentials_for_user_models(self):
        if self.source != SourceTypes.user:
            return self

        entry: ConfigTypeRegistryItem = CONFIG_TYPE_REGISTRY.get(self.type)
        if not entry or entry.section not in ['llm', 'embedding', 'image_generation']:
            return self

        ai_credentials = self.data.get('ai_credentials')
        if not ai_credentials:
            raise ValueError(
                f"AI credentials are required for user-created {entry.section} models. "
                f"Please provide valid credentials to create this model."
            )

        return self

    @model_validator(mode='after')
    def enforce_service_prompt_title(self):
        if self.type != 'service_prompt':
            return self

        key = str((self.data or {}).get('key') or '').strip().lower()
        if not key:
            return self

        # Store elitea_title as the service prompt key to guarantee uniqueness per project.
        self.elitea_title = key
        return self

    @property
    def _entry(self) -> ConfigTypeRegistryItem:
        """
        Get the registry entry for this configuration type.
        """
        entry = CONFIG_TYPE_REGISTRY.get(self.type)
        if not entry:
            raise ValueError(f"Unknown configuration type: {self.type}")
        return entry

    def make_db_model(self) -> Configuration:
        """
        Convert this Pydantic model to a database model instance.
        """
        entry: ConfigTypeRegistryItem = self._entry
        serialized_data = self.model_dump(include={'data'}, context={'unsecret': True})['data']
        # TODO: temp fix for pydantic v1
        from pydantic.v1 import SecretStr
        for k, v in serialized_data.items():
            if isinstance(v, SecretStr):
                serialized_data[k] = v.get_secret_value()
        conf = Configuration(
            project_id=self.project_id,
            elitea_title=self.elitea_title,
            label=self.label,
            type=self.type,
            section=entry.section,
            data=serialized_data,
            meta=self.meta,
            shared=self.shared,
            author_id=self.author_id,
            source=self.source,
        )
        if self.uuid:
            conf.uuid = str(self.uuid)
        return conf


class ConfigurationCreateRpc(ConfigurationCreate):
    source: SourceTypes = SourceTypes.system
    status_ok: bool = True
    author_id: None | int = None


class ConfigurationDetails(BaseModel):
    id: int
    uuid: str | UUID4
    project_id: int
    elitea_title: str
    label: Optional[str]
    type: str
    section: str
    data: dict[str, Any]
    meta: dict[str, Any] = {}
    shared: bool = False
    status_ok: bool
    status_logs: Optional[str]
    source: SourceTypes
    author_id: Optional[int]
    created_at: datetime
    updated_at: Optional[datetime]
    is_pinned: bool = False

    model_config = ConfigDict(from_attributes=True)


class ConfigurationList(ConfigurationDetails):
    ...


class ConfigurationUpdate(BaseModel):
    """Model for updating an existing configuration."""
    elitea_title: Optional[str] = Field(None, description="Unique identifier for the configuration")
    label: Optional[str] = Field(None, description="Human-readable display name")
    data: Optional[dict[str, Any]] = Field(None, description="Configuration-specific data payload")
    meta: Optional[dict[str, Any]] = Field(None, description="Additional metadata")
    shared: Optional[bool] = Field(None, description="Whether the configuration is shared across projects")

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "label": "Updated GPT-5.1 Configuration",
                    "data": {
                        "name": "gpt-5.1",
                        "low_tier": False,
                        "high_tier": True,
                        "ai_credentials": {
                            "private": False, "elitea_title": "azure_creds_public"
                        },
                        "context_window": 400000,
                        "max_output_tokens": 128000,
                        "supports_reasoning": True
                    }
                }
            ]
        }
    )

    @field_validator('data', mode='after')
    def validate_data(cls, v):
        """
        Validate configuration data payload
        """
        if v is not None:
            # validate numeric fields
            numeric_fields = ['max_output_tokens', 'context_window']
            for field in numeric_fields:
                if field in v:
                    value = v[field]
                    try:
                        int_value = int(value)
                        v[field] = int_value
                    except (ValueError, TypeError):
                        v[field] = 0
        return v


class ConfigurationUpdateRpc(BaseModel):
    elitea_title: Optional[str] = None
    label: Optional[str] = None
    section: Optional[str] = None
    data: dict[str, Any] | None = None
    meta: dict[str, Any] | None = None
    shared: Optional[bool] = None
    status_ok: Optional[bool] = None
    status_logs: Optional[str] = None

    model_config = ConfigDict(extra='forbid')
