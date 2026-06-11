from pydantic import BaseModel, ConfigDict, Field


PROJECT_CONTEXT_MAX_LEN = 2500


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
