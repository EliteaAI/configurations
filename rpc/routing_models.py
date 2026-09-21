"""Internal actor-scoped inventory used after Gateway authentication."""
from pylon.core.tools import web

from ..routing_models import get_routing_models


class RPC:
    @web.rpc('configurations_get_routing_models')
    def configurations_get_routing_models(self, project_id: int, user_id: int) -> dict:
        return get_routing_models(project_id, user_id)
