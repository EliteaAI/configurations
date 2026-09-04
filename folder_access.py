import functools
from queue import Empty
from typing import List, Optional, Union

from sqlalchemy import and_, exists

from .local_tools import rpc_manager, log


def get_restricted_folder_ids(
        project_id: int,
        entity_types: Union[str, List[str]] = 'configuration',
        user_id: Optional[int] = None,
) -> list:
    """Folders the caller has no access to.

    An absent `social` plugin means the folder feature is not installed, so an empty
    list is correct. Any other failure is *not* swallowed: returning [] there would
    silently expose restricted configurations, so the error propagates to the caller.
    """
    try:
        return rpc_manager.timeout(3).social_get_restricted_folder_ids(
            project_id=project_id,
            entity_type=entity_types,
            user_id=user_id,
        ) or []
    except Empty:
        log.debug("social_get_restricted_folder_ids unavailable, no folder filtering applied")
        return []


def folder_exclusion_clause(
        project_id: int,
        id_column,
        entity_types: Union[str, List[str]] = 'configuration',
        user_id: Optional[int] = None,
):
    """SQL predicate hiding entities that live in the caller's no-access folders (#6524).

    Returns None when nothing is restricted, so the hot path adds no subquery at all.
    Must be applied to the listing query *before* count/offset/limit, otherwise the
    total and the page size are computed over rows the user cannot see.
    """
    restricted = get_restricted_folder_ids(project_id, entity_types, user_id)
    if not restricted:
        return None
    #
    try:
        FolderItem = rpc_manager.timeout(2).social_get_folder_item_model()
    except Empty:
        log.debug("social_get_folder_item_model unavailable, no folder filtering applied")
        return None
    #
    types = [entity_types] if isinstance(entity_types, str) else list(entity_types)
    return ~exists().where(and_(
        FolderItem.entity.in_(types),
        FolderItem.entity_id == id_column,
        FolderItem.folder_id.in_(restricted),
    ))


NO_ACCESS_ERROR = 'Not found'
READ_ONLY_ERROR = 'You have read-only access to this folder'


def resolve_entity_access(
        project_id: int,
        entity_id: int,
        entity_type: str = 'configuration',
        user_id: Optional[int] = None,
) -> str:
    """Effective folder access for one configuration: 'full' | 'read_only' | 'no_access'.

    Absent `social` plugin means no folders exist, so 'full'. Every other failure
    propagates — answering 'full' on a broken lookup would hand out the entity.
    """
    try:
        return rpc_manager.timeout(3).social_resolve_entity_access(
            project_id=project_id,
            entity_type=entity_type,
            entity_id=entity_id,
            user_id=user_id,
        ) or 'full'
    except Empty:
        log.debug("social_resolve_entity_access unavailable, folder access not enforced")
        return 'full'


def entity_access_error(
        project_id: int,
        entity_id: int,
        write: bool = False,
        entity_type: str = 'configuration',
        user_id: Optional[int] = None,
):
    """None when the operation is allowed, else the `(payload, status)` to return.

    `no_access` answers 404 with the same body a missing configuration produces, so a
    restricted one is indistinguishable from a nonexistent one.
    """
    level = resolve_entity_access(project_id, entity_id, entity_type, user_id)
    if level == 'no_access':
        return {'error': NO_ACCESS_ERROR}, 404
    if write and level == 'read_only':
        return {'error': READ_ONLY_ERROR}, 403
    return None


def require_folder_access(id_param: str, write: bool = False, entity_type: str = 'configuration'):
    """Enforce folder-level access on the entity addressed by the request path (#6524).

    Placed below `@auth.decorators.check_api` so RBAC runs first: folder exceptions only
    ever narrow role-based access.
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(self, *args, **kwargs):
            project_id = kwargs.get('project_id')
            entity_id = kwargs.get(id_param)
            if project_id and entity_id:
                error = entity_access_error(
                    project_id, entity_id, write=write, entity_type=entity_type
                )
                if error is not None:
                    return error
            return func(self, *args, **kwargs)
        return wrapper
    return decorator
