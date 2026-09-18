"""Internal availability read for authenticated Core and gateway callers."""
from pylon.core.tools import web
from ..routing_settings import get_effective_settings


class RPC:
    @web.rpc('configurations_get_auto_routing_settings')
    def configurations_get_auto_routing_settings(self, project_id: int):
        return get_effective_settings(project_id)
