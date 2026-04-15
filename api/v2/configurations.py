from flask import request

from tools import api_tools
from ...local_tools import APIBase, current_user, log, auth, config as c
from ...utils import create_configuration, get_configurations
from ...exceptions import ConfigurationError


class API(APIBase):
    url_params = [
        '<int:project_id>'
    ]

    @auth.decorators.check_api(
        {
            "permissions": ["configurations.configurations.list"],
            "recommended_roles": {
                c.ADMINISTRATION_MODE: {"admin": True, "editor": True, "viewer": True},
                c.DEFAULT_MODE: {"admin": True, "editor": True, "viewer": True},
            },
        }
    )
    @api_tools.endpoint_metrics
    def get(self, project_id: int, **kwargs):
        """Get configurations with pagination, with optional separate shared configurations.
        
        Args:
            project_id (int): The ID of the project to fetch configurations for
            
        Query Parameters:
            type (str, optional): Filter by configuration type
            section (str, optional): Filter by configuration section
            offset (int, optional): Pagination offset for project configurations. Defaults to 0
            limit (int, optional): Items per page for project configurations. Defaults to 20
            include_shared (bool, optional): Whether to include shared configurations. Defaults to False
            shared_offset (int, optional): Pagination offset for shared configurations. Defaults to 0
            shared_limit (int, optional): Items per page for shared configurations. Defaults to 20
            sort_by (str, optional): Field to sort by. Defaults to "created_at"
            sort_order (str, optional): Sort order - "asc" or "desc". Defaults to "desc"
            query (str, optional): Search query to filter configurations by label

        Returns:
            tuple: (dict with project and shared configurations, HTTP status code)
        """
        type_filter = request.args.getlist("type")
        section_filter = request.args.getlist("section")

        offset = request.args.get("offset", default=0, type=int)
        limit = request.args.get("limit", default=20, type=int)

        include_shared = request.args.get("include_shared", default="false").lower() == "true"
        shared_offset = request.args.get("shared_offset", default=0, type=int)
        shared_limit = request.args.get("shared_limit", default=20, type=int)

        sort_by = request.args.get("sort_by", default="created_at")
        sort_order = request.args.get("sort_order", default='desc')

        query = request.args.get('query')

        response = get_configurations(
            project_id=project_id,
            type_filter=type_filter,
            section_filter=section_filter,
            offset=offset,
            limit=limit,
            include_shared=include_shared,
            shared_offset=shared_offset,
            shared_limit=shared_limit,
            query=query,
            sort_by=sort_by,
            sort_order=sort_order
        )

        return response, 200

    @auth.decorators.check_api(
        {
            "permissions": ["configurations.configuration.create"],
            "recommended_roles": {
                c.ADMINISTRATION_MODE: {"admin": True, "editor": True, "viewer": False},
                c.DEFAULT_MODE: {"admin": True, "editor": True, "viewer": False},
            },
        }
    )
    @api_tools.endpoint_metrics
    def post(self, project_id: int, **kwargs):
        """Create a new configuration with structured error handling."""
        data = dict(request.json)
        data['project_id'] = project_id
        data['author_id'] = current_user().get('id')

        try:
            return create_configuration(data), 200
        except ConfigurationError as ce:
            log.warning(f"Configuration error on create in field '{ce.field}': {ce.message}")
            return ce.to_dict(), 400
        except Exception:
            log.exception("Unexpected error on create during configuration creation")
            return {
                "error": f"Unexpected error",
                "field": "unknown"
            }, 500
