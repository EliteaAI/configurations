"""Project availability override; profile qualification is a separate contract."""
from pydantic import BaseModel, ConfigDict, Field, StrictBool


class AutoRoutingProjectSettings(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
        json_schema_extra={'metadata': {
            'label': 'Auto model selection', 'section': 'project_settings', 'type': 'auto_routing',
        }},
    )
    enabled: StrictBool | None = Field(
        default=None,
        description='Use the platform default when unset. A project cannot override the platform master switch.',
    )


def routing_enabled(environment: dict, project: dict | None = None) -> bool:
    """Strict fail-closed resolution, shared by snapshot publishers and tests."""
    if environment.get('auto_routing_available') is not True:
        return False
    override = (project or {}).get('enabled')
    if override is not None:
        return override is True
    return environment.get('auto_routing_project_default') is True
