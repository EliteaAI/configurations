from pydantic import BaseModel, ConfigDict, Field, StrictBool


class ProjectSkillsSettings(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
        json_schema_extra={'metadata': {
            'label': 'Project skills in chat', 'section': 'project_settings', 'type': 'project_skills',
        }},
    )
    enabled: StrictBool = Field(
        default=False,
        description='Offer every project skill to model and agent chats on demand. Off by default.',
    )
