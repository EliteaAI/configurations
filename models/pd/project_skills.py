from pydantic import BaseModel, ConfigDict, Field, StrictBool


class ProjectSkillsSettings(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
        json_schema_extra={'metadata': {
            'label': 'Project skills in chat', 'section': 'project_settings', 'type': 'project_skills',
        }},
    )
    enabled: StrictBool | None = Field(
        default=None,
        description='Use the platform default when unset. A project cannot override the platform master switch.',
    )
