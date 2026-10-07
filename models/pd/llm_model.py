from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator

EffortLevel = Literal['none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max']
ThinkingType = Literal['adaptive', 'enabled', 'always_on']
BUDGET_EFFORT_LEVELS = ('low', 'medium', 'high')


# class Capabilities(BaseModel):
#     image_processing: bool = False
#     function_calling: bool = False
#     structured_output: bool = False


class AiCredentials(BaseModel):
    elitea_title: str
    private: bool


class LlmModel(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "metadata": {
                "label": "LLM model",
                "section": "llm",
                "type": "llm_model"
            }
        }
    )
    name: str
    description: Optional[str] = Field(
        default=None,
        max_length=40,
        description="Display-only tagline shown under the model name in pickers; never sent to the provider"
    )
    context_window: int = 128000
    max_output_tokens: int = 16000
    supports_reasoning: Optional[bool] = False
    supports_vision: Optional[bool] = Field(
        default=True,
        description="Whether this LLM model supports vision/multimodal image input"
    )
    low_tier: Optional[bool] = False
    high_tier: Optional[bool] = False
    openai_compatible: Optional[bool] = False
    api_protocol: Optional[Literal['azure', 'openai', 'anthropic']] = Field(
        default=None,
        description=(
            "Upstream API protocol to route this model through (DIAL credentials only; unset means 'azure'). "
            "'azure' suits most models, including Gemini with reasoning; "
            "'anthropic' is required for Claude thinking/reasoning effort; "
            "'openai' targets the OpenAI Responses API and is not supported for Claude models"
        )
    )

    thinking_type: Optional[ThinkingType] = Field(
        default=None,
        description=(
            "Anthropic thinking mode. 'adaptive': the model decides how much to think; "
            "'enabled': legacy token budget for Claude 4.5 and older; "
            "'always_on': adaptive and the provider rejects turning it off (Fable, Mythos, Opus 5.5). "
            "Null for non-Anthropic models and for rows configured before this field existed"
        )
    )
    supported_efforts: Optional[list[EffortLevel]] = Field(
        default=None,
        description=(
            "Effort levels users may pick for this model. Null keeps today's behaviour (low, medium, high). "
            "With thinking_type 'enabled' the levels map to a thinking token budget"
        )
    )
    default_effort: Optional[EffortLevel] = Field(
        default=None,
        validate_default=True,
        description="Effort used when a user has not chosen one; must be one of supported_efforts and never 'none'"
    )

    ai_credentials: Optional[AiCredentials] = Field(
        default=None,
        json_schema_extra={'configuration_sections': ['ai_credentials',],}
    )

    @field_validator('description', mode='before')
    @classmethod
    def blank_description_to_none(cls, value):
        if isinstance(value, str):
            return value.strip() or None
        return value

    @field_validator('thinking_type', 'supported_efforts', 'default_effort')
    @classmethod
    def reasoning_fields_need_reasoning_support(cls, value, info: ValidationInfo):
        if value is not None and not info.data.get('supports_reasoning'):
            raise ValueError("requires 'Supports Reasoning' to be enabled")
        return value

    @field_validator('supported_efforts')
    @classmethod
    def validate_supported_efforts(cls, value, info: ValidationInfo):
        if value is None:
            return value
        levels = list(dict.fromkeys(value))
        if not levels:
            raise ValueError('at least one effort level is required')
        thinking_type = info.data.get('thinking_type')
        if thinking_type == 'enabled':
            beyond_budget = [level for level in levels if level not in BUDGET_EFFORT_LEVELS]
            if beyond_budget:
                raise ValueError(
                    f"thinking_type 'enabled' maps levels to a token budget and only accepts "
                    f"{', '.join(BUDGET_EFFORT_LEVELS)}; remove {', '.join(beyond_budget)}"
                )
        if thinking_type == 'always_on' and 'none' in levels:
            raise ValueError("thinking_type 'always_on' cannot offer 'none'")
        return levels

    @field_validator('default_effort')
    @classmethod
    def validate_default_effort(cls, value, info: ValidationInfo):
        supported = info.data.get('supported_efforts')
        if value is None:
            if supported:
                raise ValueError('required when supported_efforts is set')
            return value
        if value == 'none':
            raise ValueError("cannot be 'none'")
        if not supported:
            raise ValueError('requires supported_efforts')
        if value not in supported:
            raise ValueError(f"must be one of supported_efforts: {', '.join(supported)}")
        return value


class EmbeddingModel(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "metadata": {
                "label": "Embedding model",
                "section": "embedding",
                "type": "embedding_model"
            }
        }
    )
    name: str

    ai_credentials: Optional[AiCredentials] = Field(
        default=None,
        json_schema_extra={'configuration_sections': ['ai_credentials',],}
    )


class ImageGenerationModel(BaseModel):
    """Configuration for image generation models (e.g., DALL-E, Stable Diffusion)."""

    model_config = ConfigDict(
        json_schema_extra={
            "metadata": {
                "label": "Image Generation Model",
                "section": "image_generation",
                "type": "image_generation_model"
            }
        }
    )

    name: str

    ai_credentials: Optional[AiCredentials] = Field(
        default=None,
        json_schema_extra={'configuration_sections': ['ai_credentials']}
    )

class ASRModel(BaseModel):
    """Configuration for Automatic Speech Recognition (ASR) models."""

    model_config = ConfigDict(
        json_schema_extra={
            "metadata": {
                "label": "Speech Recognition (ASR) Model",
                "section": "asr",
                "type": "asr_model"
            }
        }
    )

    name: str

    ai_credentials: Optional[AiCredentials] = Field(
        default=None,
        json_schema_extra={'configuration_sections': ['ai_credentials']}
    )


class LlmModelList(BaseModel):
    name: str
    display_name: str
    description: Optional[str] = None
    project_id: int
    shared: bool = False
    context_window: int = 128000
    max_output_tokens: int = 16000
    supports_reasoning: Optional[bool] = False
    supports_vision: Optional[bool] = True
    low_tier: Optional[bool] = False
    high_tier: Optional[bool] = False
    openai_compatible: Optional[bool] = False
    api_protocol: Literal['azure', 'openai', 'anthropic'] = 'azure'
    thinking_type: Optional[str] = None
    supported_efforts: Optional[list[str]] = None
    default_effort: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)

class EmbeddingModelList(BaseModel):
    name: str
    display_name: str
    project_id: int
    shared: bool = False

    model_config = ConfigDict(from_attributes=True)


class ImageGenerationModelList(BaseModel):
    """Response model for image generation model listings."""
    name: str
    display_name: str
    project_id: int
    shared: bool = False

    model_config = ConfigDict(from_attributes=True)


class ASRModelList(BaseModel):
    """Response model for ASR model listings."""
    name: str
    display_name: str
    project_id: int
    shared: bool = False

    model_config = ConfigDict(from_attributes=True)


class TTSModel(BaseModel):
    """Configuration for Text-to-Speech (TTS) models."""

    model_config = ConfigDict(
        json_schema_extra={
            "metadata": {
                "label": "Text to Speech (TTS) Model",
                "section": "tts",
                "type": "tts_model"
            }
        }
    )

    name: str

    ai_credentials: Optional[AiCredentials] = Field(
        default=None,
        json_schema_extra={'configuration_sections': ['ai_credentials']}
    )

    @staticmethod
    def check_connection(settings: dict) -> dict | str | None:
        """
        Fetch available voices for the TTS configuration.

        Returns:
            - dict with {'voices': [...]} on success (list may be empty for
              providers with no enumerable catalogue)
            - str with a generic error message on failure (detail is logged,
              not surfaced, to avoid leaking internal error messages to clients)
            - None if the check is not supported
        """
        try:
            from pylon.core.tools import log as _log
            from ...utils_tts_voices import fetch_tts_voices

            voices = fetch_tts_voices(settings)
            return {'voices': voices}

        except Exception as e:
            from pylon.core.tools import log as _log
            _log.error("TTS check_connection failed: %s", e)
            return "Could not connect to TTS provider. Check credentials and configuration."


class TTSModelList(BaseModel):
    """Response model for TTS model listings."""
    name: str
    display_name: str
    project_id: int
    shared: bool = False

    model_config = ConfigDict(from_attributes=True)


class VectorStorageModelList(BaseModel):
    name: str = Field(alias='elitea_title')
    project_id: int
    shared: bool = False

    model_config = ConfigDict(from_attributes=True)


class SetDefaultModel(BaseModel):
    name: Optional[str] = None
    target_project_id: Optional[int] = None
    section: Optional[str] = Field(default='llm')
    mode: Literal['fixed', 'auto'] = 'fixed'

    @model_validator(mode='after')
    def validate_default_selection(self):
        if self.mode == 'auto':
            if self.section != 'llm' or self.name is not None or self.target_project_id is not None:
                raise ValueError('Auto is only an LLM default intent, without a fixed model binding')
        elif not self.name or self.target_project_id is None:
            raise ValueError('A concrete default requires name and target_project_id')
        return self

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "name": "gpt-5.1",
                    "target_project_id": 2,
                    "section": "llm"
                }
            ]
        }
    )
