from typing import Optional

from pylon.core.tools import log
from tools import config as c

from .local_tools import rpc_manager


# Redis cache for public project ID - mirrors elitea_core implementation
_PUBLIC_PROJECT_ID_CACHE_KEY = "elitea:config:ai_project_id"
_PUBLIC_PROJECT_ID_TTL = 86400  # 24 hours


def get_public_project_id() -> int:
    """Get the public project ID with Redis caching.

    Reads from elitea_core plugin config via cross-plugin access.
    """
    # Try Redis cache first
    redis_client = None
    try:
        from tools import auth
        redis_client = auth.get_cache_redis_client()
        #
        if redis_client:
            cached_value = redis_client.get(_PUBLIC_PROJECT_ID_CACHE_KEY)
            #
            if cached_value is not None:
                return int(cached_value)
    except:
        pass

    # Read from elitea_core plugin config
    from tools import elitea_config  # pylint: disable=C0415,E0401
    project_id = elitea_config.get("ai_project_id", 1)
    project_id_int = int(project_id)

    # Cache in Redis
    if redis_client:
        try:
            redis_client.setex(_PUBLIC_PROJECT_ID_CACHE_KEY, _PUBLIC_PROJECT_ID_TTL, project_id_int)
        except Exception:
            pass

    return project_id_int


def get_personal_project_id(user_id: int) -> int:
    return int(rpc_manager.call.projects_get_personal_project_id(user_id=user_id))
