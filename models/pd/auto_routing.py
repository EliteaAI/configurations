"""Project availability override; profile qualification is a separate contract."""
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StrictInt, model_validator


class ClassifierRef(BaseModel):
    """One exact deployment (model name + owner project) that classifies Auto requests (#6826)."""
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=512)
    project_id: StrictInt = Field(gt=0)


# --- Auto routing policy (#6722): every value the router used to hard-code ------------------------------------
# Absolute ceiling for token-valued settings; the platform value is the ceiling for projects.
MAX_TOKENS = 128000
_ARTIFACT_ID = r'^[A-Za-z0-9][A-Za-z0-9._-]*$'


class _Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')


class SelectorPolicy(_Strict):
    artifact: str | None = Field(default=None, min_length=1, max_length=128, pattern=_ARTIFACT_ID,
                                 description='Pinned selector artifact id; must match the artifact installed in the Gateway.')
    tau: StrictFloat = Field(default=0.85, ge=0.5, le=0.99, description='Minimum predicted pass probability.')


class OutputPolicy(_Strict):
    default_allowance: StrictInt = Field(default=8000, ge=256, le=MAX_TOKENS)
    min_cap: StrictInt = Field(default=256, ge=1, le=MAX_TOKENS)
    max_cap: StrictInt = Field(default=32000, ge=1, le=MAX_TOKENS)

    @model_validator(mode='after')
    def _ordered(self):
        if self.min_cap > self.max_cap:
            raise ValueError('min_cap must not exceed max_cap')
        return self


class ThinkingBudgets(_Strict):
    low: StrictInt = Field(default=2048, ge=0, le=MAX_TOKENS)
    medium: StrictInt = Field(default=4096, ge=0, le=MAX_TOKENS)
    high: StrictInt = Field(default=9092, ge=0, le=MAX_TOKENS)


class ClassifierPolicy(_Strict):
    enabled: StrictBool = False


PoolEntries = list[Annotated[str, Field(min_length=1, max_length=512)]] | None


def _pool():
    return Field(default=None, min_length=1, max_length=256,
                 description='Canonical ids or exact deployment names; null admits every deployment.')


class AutoRoutingPolicy(_Strict):
    """Complete policy; defaults equal the values that were literals in the Gateway."""
    version: Literal[1] = 1
    shadow_mode: StrictBool = True
    selector: SelectorPolicy = Field(default_factory=SelectorPolicy)
    pool: PoolEntries = _pool()
    fallback: ClassifierRef | None = Field(default=None, description='Deployment used when nothing qualifies.')
    output: OutputPolicy = Field(default_factory=OutputPolicy)
    thinking_budget_tokens: ThinkingBudgets = Field(default_factory=ThinkingBudgets)
    greeting_rule: StrictBool = True
    classifier: ClassifierPolicy = Field(default_factory=ClassifierPolicy)


# Project overrides: any subset. Omitted means inherit; an explicit null is meaningful only for
# artifact, pool and fallback (the model_dump(exclude_unset=True) form is what gets stored).
class SelectorOverride(_Strict):
    artifact: str | None = Field(default=None, min_length=1, max_length=128, pattern=_ARTIFACT_ID)
    tau: StrictFloat = Field(default=None, ge=0.5, le=0.99)


class OutputOverride(_Strict):
    default_allowance: StrictInt = Field(default=None, ge=256, le=MAX_TOKENS)
    min_cap: StrictInt = Field(default=None, ge=1, le=MAX_TOKENS)
    max_cap: StrictInt = Field(default=None, ge=1, le=MAX_TOKENS)

    @model_validator(mode='after')
    def _ordered(self):
        if self.min_cap is not None and self.max_cap is not None and self.min_cap > self.max_cap:
            raise ValueError('min_cap must not exceed max_cap')
        return self


class ThinkingBudgetsOverride(_Strict):
    low: StrictInt = Field(default=None, ge=0, le=MAX_TOKENS)
    medium: StrictInt = Field(default=None, ge=0, le=MAX_TOKENS)
    high: StrictInt = Field(default=None, ge=0, le=MAX_TOKENS)


class ClassifierPolicyOverride(_Strict):
    enabled: StrictBool = None


class AutoRoutingPolicyOverride(_Strict):
    shadow_mode: StrictBool = None
    selector: SelectorOverride = None
    pool: PoolEntries = _pool()
    fallback: ClassifierRef | None = None
    output: OutputOverride = None
    thinking_budget_tokens: ThinkingBudgetsOverride = None
    greeting_rule: StrictBool = None
    classifier: ClassifierPolicyOverride = None


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
    policy: AutoRoutingPolicyOverride | None = Field(
        default=None,
        description='Partial Auto routing policy; omitted keys inherit the platform policy. '
                    'Cannot exceed platform limits or leave shadow mode without platform permission.',
    )


def routing_enabled(environment: dict, project: dict | None = None) -> bool:
    """Strict fail-closed resolution, shared by snapshot publishers and tests."""
    if environment.get('auto_routing_available') is not True:
        return False
    override = (project or {}).get('enabled')
    if override is not None:
        return override is True
    return environment.get('auto_routing_project_default') is True
