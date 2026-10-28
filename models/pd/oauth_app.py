"""Admin-managed OAuth apps (#7149): one app serves every credential of its provider in Default app mode.

``provider`` names the identity provider. Only Microsoft Entra ID exists today (Teams,
Outlook, SharePoint); a new provider adds an ``OAuthProvider`` entry here and its own
connection check, and the storage, write-only secret, project allow-list and Default /
Custom app modes are reused unchanged.
"""
import re
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, StrictBool, field_validator, model_validator
from urllib.parse import urlparse

OAUTH_APP_TYPE = 'oauth_app'
OAUTH_APP_SECTION = 'oauth_apps'
MICROSOFT_ENTRA = 'microsoft_entra'

ProviderId = Literal['microsoft_entra']


@dataclass(frozen=True)
class OAuthProvider:
    label: str
    # Credential types that can reference an app of this provider instead of carrying their own client
    credential_types: tuple[str, ...]
    # The app's client secret is posted only to these hosts
    hosts: frozenset[str]
    default_discovery_endpoint: str
    discovery_endpoint_hint: str
    token_path: str
    # Types an app serves when its credential_types is unset (apps saved before a type was added);
    # a type left out is opt-in: the admin switches it on per app. Empty means every type.
    default_credential_types: tuple[str, ...] = ()
    # Types whose scopes are free text, checked only to be the provider's own permissions
    free_scope_types: tuple[str, ...] = ()


OAUTH_PROVIDERS: dict[str, OAuthProvider] = {
    MICROSOFT_ENTRA: OAuthProvider(
        label='Microsoft Entra ID',
        credential_types=('teams', 'outlook', 'sharepoint', 'ms_graph'),
        hosts=frozenset({'login.microsoftonline.com'}),
        default_discovery_endpoint='https://login.microsoftonline.com/organizations',
        discovery_endpoint_hint='https://login.microsoftonline.com/<tenant_id or organizations>',
        token_path='/oauth2/v2.0/token',
        # Microsoft Graph (low-level, any endpoint) is switched on per app by the admin
        default_credential_types=('teams', 'outlook', 'sharepoint'),
        free_scope_types=('ms_graph',),
    ),
}

# Credential type -> provider of the apps it may reference
CREDENTIAL_PROVIDERS = {
    credential_type: provider_id
    for provider_id, provider in OAUTH_PROVIDERS.items()
    for credential_type in provider.credential_types
}
OAUTH_APP_TOOLKIT_TYPES = tuple(CREDENTIAL_PROVIDERS)


GRAPH_RESOURCE_PREFIX = 'https://graph.microsoft.com/'
GRAPH_DEFAULT_SCOPE = f'{GRAPH_RESOURCE_PREFIX}.default'
_GRAPH_PERMISSION_RE = re.compile(r'^[A-Za-z][A-Za-z0-9_-]*(\.[A-Za-z0-9_-]+)+$')
_OIDC_SCOPES = {'openid', 'profile', 'email', 'offline_access'}


def normalize_graph_scopes(scopes: list[str]) -> list[str]:
    """Free-text Microsoft Graph permissions (mirrors elitea-sdk ms_graph): prefix stripped, OIDC scopes
    and duplicates dropped, other resources rejected — the token is for Microsoft Graph only."""
    result, seen = [], set()
    for scope in scopes:
        text = scope.strip()
        if not text or text.lower() in _OIDC_SCOPES:
            continue
        if text.lower().startswith(GRAPH_RESOURCE_PREFIX):
            text = text[len(GRAPH_RESOURCE_PREFIX):]
        if text.lower() == '.default':
            text = GRAPH_DEFAULT_SCOPE
        elif not _GRAPH_PERMISSION_RE.match(text):
            raise ValueError(f"'{scope}' is not a Microsoft Graph permission, e.g. Mail.Read")
        if text.lower() not in seen:
            seen.add(text.lower())
            result.append(text)
    if GRAPH_DEFAULT_SCOPE in result and len(result) > 1:
        raise ValueError(f'{GRAPH_DEFAULT_SCOPE} cannot be combined with other permissions')
    return result


def provider_of(data: dict | None) -> OAuthProvider:
    """Records saved before ``provider`` existed are Microsoft Entra ID apps."""
    return OAUTH_PROVIDERS[(data or {}).get('provider') or MICROSOFT_ENTRA]


def is_provider_authority(provider: OAuthProvider, url) -> bool:
    """https on one of the provider's login hosts only: the app's client secret is posted there."""
    try:
        parsed = urlparse(str(url or '').strip())
    except ValueError:
        return False
    return parsed.scheme == 'https' and (parsed.hostname or '').lower() in provider.hosts and not parsed.username


def token_endpoint(data: dict) -> str:
    provider = provider_of(data)
    authority = (data.get('oauth_discovery_endpoint') or provider.default_discovery_endpoint).rstrip('/')
    return f'{authority}{provider.token_path}'


class OAuthApp(BaseModel):
    """Client, authority, scopes and token refresh of one app, set here and not per credential:
    a Default-mode credential carries nothing but the app reference.
    """
    model_config = ConfigDict(
        extra='forbid',
        json_schema_extra={'metadata': {
            'label': 'OAuth app', 'section': OAUTH_APP_SECTION, 'type': OAUTH_APP_TYPE,
        }},
    )

    provider: ProviderId = Field(default=MICROSOFT_ENTRA, description='Identity provider; fixed after creation')
    client_id: str = Field(min_length=1, description='Application (client) ID')
    client_secret: SecretStr = Field(description='Client secret value', json_schema_extra={'format': 'password'})
    oauth_discovery_endpoint: str | None = Field(
        default=None,
        description="Authority, e.g. https://login.microsoftonline.com/<tenant_id>; empty uses the provider's default",
    )
    credential_types: list[str] | None = Field(
        default=None,
        description="Credential types of the provider this app serves; unset means all of them")
    scopes: dict[str, list[str]] = Field(
        default_factory=dict,
        description="Scopes requested for each credential type; empty uses the type's recommended set")
    auto_refresh_token: StrictBool = Field(
        default=True, description='Refresh access tokens with a refresh token instead of asking users to sign in')
    is_default: StrictBool = Field(
        default=False, description="Users of this provider's credentials get this app; one per provider")
    enabled: StrictBool = True
    allowed_project_ids: list[int] = Field(
        min_length=1,
        description='Projects that may use this app (required). The public project means every project')

    @field_validator('client_id')
    @classmethod
    def _strip_client_id(cls, value):
        value = value.strip()
        if not value:
            raise ValueError('Client ID is required')
        return value

    @field_validator('allowed_project_ids')
    @classmethod
    def _unique(cls, value):
        value = list(dict.fromkeys(int(i) for i in value))
        if not value:
            raise ValueError('Select at least one project')
        return value

    @field_validator('scopes')
    @classmethod
    def _clean_scopes(cls, value):
        cleaned = {key: [s.strip() for s in scopes if s and s.strip()] for key, scopes in value.items()}
        return {key: scopes for key, scopes in cleaned.items() if scopes}

    @model_validator(mode='after')
    def _match_provider(self):
        provider = OAUTH_PROVIDERS[self.provider]
        endpoint = (self.oauth_discovery_endpoint or '').strip().rstrip('/') or provider.default_discovery_endpoint
        if not is_provider_authority(provider, endpoint):
            raise ValueError(f'OAuth discovery endpoint must be {provider.discovery_endpoint_hint}')
        self.oauth_discovery_endpoint = endpoint
        unknown = set(self.scopes) - set(provider.credential_types)
        if unknown:
            raise ValueError(f"{provider.label} apps have no scopes for: {', '.join(sorted(unknown))}")
        for credential_type in provider.free_scope_types:
            if credential_type in self.scopes:
                self.scopes[credential_type] = normalize_graph_scopes(self.scopes[credential_type])
        self.scopes = {key: scopes for key, scopes in self.scopes.items() if scopes}
        if self.credential_types is not None:
            types = list(dict.fromkeys(self.credential_types))
            foreign = set(types) - set(provider.credential_types)
            if foreign:
                raise ValueError(f"{provider.label} apps cannot serve: {', '.join(sorted(foreign))}")
            if not types:
                raise ValueError('Enable at least one credential type for this app')
            # Keep the provider's order
            self.credential_types = [t for t in provider.credential_types if t in types]
        return self


def serves(app: dict | None, credential_type: str | None) -> bool:
    """Whether the app is switched on for this credential type (unset = the provider's default types)."""
    if not credential_type:
        return True
    types = (app or {}).get('credential_types')
    if types is None:
        return credential_type in default_credential_types(provider_of(app))
    return credential_type in types


def default_credential_types(provider: OAuthProvider) -> tuple[str, ...]:
    return provider.default_credential_types or provider.credential_types
