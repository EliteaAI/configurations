from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


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
    context_window: int = 128000
    max_output_tokens: int = 16000
    supports_reasoning: Optional[bool] = False
    supports_vision: Optional[bool] = Field(
        default=True,
        description="Whether this LLM model supports vision/multimodal image input"
    )
    low_tier: Optional[bool] = False
    high_tier: Optional[bool] = False

    ai_credentials: Optional[AiCredentials] = Field(
        default=None,
        json_schema_extra={'configuration_sections': ['ai_credentials',],}
    )


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


class LlmModelList(BaseModel):
    name: str
    display_name: str
    project_id: int
    shared: bool = False
    context_window: int = 128000
    max_output_tokens: int = 16000
    supports_reasoning: Optional[bool] = False
    supports_vision: Optional[bool] = True
    low_tier: Optional[bool] = False
    high_tier: Optional[bool] = False

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


class VectorStorageModelList(BaseModel):
    name: str = Field(alias='elitea_title')
    project_id: int
    shared: bool = False

    model_config = ConfigDict(from_attributes=True)


class SetDefaultModel(BaseModel):
    name: str
    target_project_id: int
    section: Optional[str] = Field(default='llm')
