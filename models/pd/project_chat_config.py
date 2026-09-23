from typing import Any, Optional

from pydantic import BaseModel, ConfigDict


class ProjectChatConfig(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "metadata": {
                "label": "Project Chat Configuration",
                "section": "project_settings",
                "type": "project_chat_config",
            }
        }
    )

    chat_config: Optional[Any] = None
