from flask import request

from tools import api_tools
from ...local_tools import APIBase, current_user, log, auth, config as c, register_openapi
from ...models.pd.configuration import ConfigurationCreateBase
from ...utils import create_configuration, get_configurations
from ...exceptions import ConfigurationError


class API(APIBase):
    url_params = [
        '<int:project_id>'
    ]

    @register_openapi(
        name="List Configurations",
        description="List project configurations with filtering, pagination, and optional shared entries.",
        mcp_tool=True,
        mcp_description="Use this tool when you need to browse, search, or filter stored configurations in a project, optionally including shared/public ones. Do not use this tool when you need the schema of supported configuration types — use List Available Configuration Types. Do not use when you only need distinct type names already used in the project — use List Configuration Types. This is the main configuration inventory endpoint and should be the default choice when an LLM needs to find configuration records before reading or updating a specific one.",
        parameters=[
            {"name": "type", "in": "query", "schema": {"type": "string"},
             "description": "Filter by configuration type. Can be passed multiple times."},
            {"name": "section", "in": "query", "schema": {"type": "string"},
             "description": "Filter by section. Can be passed multiple times."},
            {"name": "offset", "in": "query", "schema": {"type": "integer", "default": 0},
             "description": "Pagination offset for project configurations."},
            {"name": "limit", "in": "query", "schema": {"type": "integer", "default": 20},
             "description": "Pagination limit for project configurations."},
            {"name": "include_shared", "in": "query", "schema": {"type": "boolean", "default": False},
             "description": "Include shared configurations."},
            {"name": "shared_offset", "in": "query", "schema": {"type": "integer", "default": 0},
             "description": "Pagination offset for shared configurations."},
            {"name": "shared_limit", "in": "query", "schema": {"type": "integer", "default": 20},
             "description": "Pagination limit for shared configurations."},
            {"name": "sort_by", "in": "query", "schema": {"type": "string", "default": "created_at"},
             "description": "Sort field."},
            {"name": "sort_order", "in": "query", "schema": {"type": "string", "default": "desc"},
             "description": "Sort order (asc or desc)."},
            {"name": "query", "in": "query", "schema": {"type": "string"},
             "description": "Search string for configuration label."},
        ],
        available_to_users=True,
    )
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

    @register_openapi(
        name="Create Configuration",
        description="Create a new configuration for the project.",
        mcp_tool=True,
        mcp_description="Use this tool when you want to create a new project configuration such as a model definition, credential, service prompt, or project-level setting. Do not use this tool to set the default model for a section — use Set Default Model, which changes Vault defaults rather than creating a record. Do not use when you first need to know what input schema a type expects — call List Available Configuration Types first. This is the primary creation endpoint for configuration records and is best used after schema discovery and optional pre-validation.",
        parameters=[
            {"name": "project_id", "in": "path", "schema": {"type": "integer"},
             "description": "Project identifier."},
        ],
        request_body=ConfigurationCreateBase,
        available_to_users=True,
    )
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
