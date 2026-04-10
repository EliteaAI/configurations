from re import match

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .service_prompt_keys import SERVICE_PROMPT_KEYS
from .service_prompt_defaults import SERVICE_PROMPT_DEFAULTS


class ServicePrompt(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "metadata": {
                "label": "Service prompt",
                "section": "service_prompts",
                "type": "service_prompt",
            }
        }
    )

    key: str = Field(
        title="Key",
        description="Stable key used by services to locate this prompt (e.g. mermaid_quick_fix)",
        json_schema_extra={
            # Exposed to frontend via /configurations/available so UI can render a dropdown.
            "enum": list(SERVICE_PROMPT_KEYS),
        },
    )
    prompt: str = Field(
        title="Prompt",
        description="Base prompt text that will be concatenated with runtime context.",
        min_length=1,
        json_schema_extra={
            # Exposed to frontend via /configurations/available so UI can restore defaults per key.
            "default_by_key": SERVICE_PROMPT_DEFAULTS,
        },
    )

    @field_validator("key", mode="after")
    @classmethod
    def validate_key(cls, v: str) -> str:
        if not v:
            raise ValueError("Key cannot be empty")
        if len(v) > 128:
            raise ValueError("Key must not exceed 128 characters")
        if not match(r"^[a-zA-Z0-9_-]+$", v):
            raise ValueError(
                "Key must contain only alphanumeric characters, underscores, or dashes (no spaces)"
            )

        normalized = v.lower()
        if normalized not in SERVICE_PROMPT_KEYS:
            raise ValueError(f"Unknown service prompt key: '{normalized}'")

        return normalized
