from pydantic import BaseModel, ConfigDict, Field, StrictBool


class EnvironmentSettings(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "metadata": {
                "label": "Environment Settings",
                "section": "environment_settings",
                "type": "environment_settings",
            }
        }
    )

    auto_routing_available: StrictBool = Field(
        default=False,
        title="Enable Auto model selection",
        description="Allow projects to offer Auto for chats and ordinary Agents. Pipelines keep explicit model selection in this release.",
    )
    auto_routing_project_default: StrictBool = Field(
        default=False,
        title="Enable Auto by default for projects",
        description="Project default while Auto model selection is enabled. Project admins may opt out or opt in.",
    )

    system_sender_name: str = Field(
        default="Elitea",
        title="System Sender Name",
        description="Display name shown as the sender for system messages.",
        min_length=1,
    )
    error_toast_duration: int = Field(
        default=10000,
        title="Error Toast Duration (ms)",
        description="How long error toast notifications are shown, in milliseconds.",
        ge=1000,
        le=30000,
    )
    warning_toast_duration: int = Field(
        default=7000,
        title="Warning Toast Duration (ms)",
        description="How long warning toast notifications are shown, in milliseconds.",
        ge=1000,
        le=30000,
    )
    success_toast_duration: int = Field(
        default=3000,
        title="Success Toast Duration (ms)",
        description="How long success toast notifications are shown, in milliseconds.",
        ge=1000,
        le=30000,
    )
    info_toast_duration: int = Field(
        default=3000,
        title="Info Toast Duration (ms)",
        description="How long info toast notifications are shown, in milliseconds.",
        ge=1000,
        le=30000,
    )
