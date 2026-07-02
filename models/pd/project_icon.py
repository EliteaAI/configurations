from typing import Optional

from pydantic import BaseModel, ConfigDict


class IconMeta(BaseModel):
    name: Optional[str] = None
    url: Optional[str] = None


class ProjectIcon(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "metadata": {
                "label": "Project Icon",
                "section": "project_settings",
                "type": "project_icon",
            }
        }
    )

    icon_meta: Optional[IconMeta] = None
