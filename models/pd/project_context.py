from pydantic import BaseModel, ConfigDict, Field


PROJECT_CONTEXT_MAX_LEN = 2500
PROJECT_CONTEXT_ACTIVATION_DESCRIPTION_MAX_LEN = 300


class ProjectContext(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "metadata": {
                "label": "Project Context",
                "section": "project_settings",
                "type": "project_context",
            }
        }
    )

    content: str = Field(
        '',
        max_length=PROJECT_CONTEXT_MAX_LEN,
        description=f"Project-level Markdown context injected into agent/LLM instructions at runtime. Max {PROJECT_CONTEXT_MAX_LEN} characters.",
    )
    enabled: bool = True
    activation_description: str | None = Field(
        default=None,
        max_length=PROJECT_CONTEXT_ACTIVATION_DESCRIPTION_MAX_LEN,
        description=(
            "Concise description of the user requests that should load the full Project Context. "
            f"Max {PROJECT_CONTEXT_ACTIVATION_DESCRIPTION_MAX_LEN} characters."
        ),
    )
