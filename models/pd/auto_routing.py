"""Project availability override; profile qualification is a separate contract."""
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt


class ClassifierRef(BaseModel):
    """One exact deployment (model name + owner project) that classifies Auto requests (#6826)."""
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=512)
    project_id: StrictInt = Field(gt=0)


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
    classifier: ClassifierRef | None = Field(
        default=None,
        description='Model that classifies Auto requests. Unset inherits the platform default classifier.',
    )


def routing_enabled(environment: dict, project: dict | None = None) -> bool:
    """Strict fail-closed resolution, shared by snapshot publishers and tests."""
    if environment.get('auto_routing_available') is not True:
        return False
    override = (project or {}).get('enabled')
    if override is not None:
        return override is True
    return environment.get('auto_routing_project_default') is True
