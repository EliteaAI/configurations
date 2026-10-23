"""Project default context window mode for new chats (#6898)."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ContextWindowProjectSettings(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
        json_schema_extra={'metadata': {
            'label': 'Context window', 'section': 'project_settings', 'type': 'context_window_settings',
        }},
    )
    default_mode: Literal['balanced', 'full'] | None = Field(
        default=None,
        description='Context window mode for new chats. Unset means balanced.',
    )
