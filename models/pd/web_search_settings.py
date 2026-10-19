"""Platform default web-search toolkit, stored in the public project (#6982)."""
from pydantic import BaseModel, ConfigDict, Field, StrictInt


class WebSearchProjectSettings(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
        json_schema_extra={'metadata': {
            'label': 'Web Search', 'section': 'project_settings', 'type': 'web_search_settings',
        }},
    )
    toolkit_id: StrictInt | None = Field(
        default=None,
        gt=0,
        description='Shared public-project toolkit used by the Web Search toggle. Unset turns the toggle off.',
    )
