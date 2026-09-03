"""Who may create or edit an observability/tracing credential.

This is containment, not a repair. The defect is that the runtime treats "a
langfuse credential exists in this project" as "tracing is enabled for this
project", so any stored credential silently redirects every project member's
prompts, responses and tool arguments to whoever supplied it. Separating
storage from activation is the actual fix; until that exists, this narrows who
can supply one to people who already control the project's data. Credentials
planted before this gate are unaffected and keep working.

Decisions recorded so they are not re-litigated in review:

* "project admin OR own personal project", not a plain admin check: personal
  projects grant their owner ``editor`` + ``viewer`` only (see
  ``create_personal_project``), so admin-only would lock owners out of their
  own private project.
* An imperative check rather than a new permission string: project permission
  resolution short-circuits on ``project_role_permission`` overrides, so a
  project that ever customised its roles would not observe a newly registered
  permission without a backfill migration, and would lock out its own admins
  until that migration ran.
* A literal type name as fallback: ``metadata.categories`` reaches the registry
  only through the indexer's schema-collection event, so testing categories
  alone fails *open* whenever that registration is degraded.
* Writes and the type catalogue only. Existing rows are hidden in the UI rather
  than by filtering the configurations list, because the agent runtime reads
  that same list authenticated as the invoking user: a server-side filter
  resolves nothing for editors and viewers and silently kills the project's
  tracing for everyone but admins.
"""

from .local_tools import current_user, log, rpc_manager
from .models.pd.registry import CONFIG_TYPE_REGISTRY

TRACING_CATEGORY = 'tracing'
TRACING_TYPE_FALLBACK = frozenset({'langfuse'})
ADMIN_ROLE_NAMES = frozenset({'admin', 'super_admin', 'system'})

_RPC_TIMEOUT = 5


def list_tracing_types() -> set[str]:
    tracing_types = set(TRACING_TYPE_FALLBACK)
    for type_name, entry in CONFIG_TYPE_REGISTRY.items():
        metadata = (entry.config_schema or {}).get('metadata') or {}
        categories = metadata.get('categories') or []
        if any(str(category).lower() == TRACING_CATEGORY for category in categories):
            tracing_types.add(type_name)
    return tracing_types


def is_tracing_type(config_type: str | None) -> bool:
    return bool(config_type) and config_type in list_tracing_types()


def current_actor_id() -> int | None:
    """Returns None rather than raising: the type catalogue is unauthenticated by design."""
    try:
        return current_user().get('id')
    except Exception as e:
        log.debug(f'No authenticated user in configurations request: {e}')
        return None


def is_own_personal_project(project_id: int, user_id: int | None) -> bool:
    if not user_id:
        return False
    try:
        personal_project_id = rpc_manager.timeout(_RPC_TIMEOUT).projects_get_personal_project_id(
            user_id=user_id
        )
        return personal_project_id is not None and int(project_id) == int(personal_project_id)
    except Exception as e:
        log.warning(f'Unable to resolve personal project for user {user_id}: {e}')
        return False


def is_project_admin(project_id: int, user_id: int) -> bool:
    # Not admin_check_user_is_admin: its `'admin' in name.lower()` test also accepts
    # project-defined roles such as "billing-admin" that carry none of an admin's authority
    try:
        roles = rpc_manager.timeout(_RPC_TIMEOUT).admin_get_user_roles(
            project_id=project_id, user_id=user_id
        ) or []
        return any(role.get('name') in ADMIN_ROLE_NAMES for role in roles)
    except Exception as e:
        log.warning(f'Unable to resolve admin role for user {user_id} in project {project_id}: {e}')
        return False


def can_manage_tracing(project_id: int, user_id: int | None) -> bool:
    if not user_id:
        return False
    # Independent signals: a failing role lookup must not also revoke a personal-project
    # owner's access to their own project
    return is_project_admin(project_id, user_id) or is_own_personal_project(project_id, user_id)


def hidden_tracing_types(project_id: int, user_id: int | None) -> set[str]:
    if can_manage_tracing(project_id, user_id):
        return set()
    return list_tracing_types()
