from flask import request

from ...oauth_apps import credential_mode, list_available_apps
from ...local_tools import APIBase, auth, config as c, register_openapi
from ...models.pd.oauth_app import OAUTH_APP_TOOLKIT_TYPES


class API(APIBase):
    url_params = [
        '<int:project_id>',
    ]

    @register_openapi(
        name="List OAuth Apps",
        description="Admin-managed OAuth apps a credential of the given type may use in this project "
                    "(only apps of that type's provider). Never includes client secrets.",
        parameters=[
            {"name": "project_id", "in": "path", "schema": {"type": "integer"},
             "description": "Project identifier."},
            {"name": "type", "in": "query", "schema": {"type": "string", "enum": list(OAUTH_APP_TOOLKIT_TYPES)},
             "description": "Credential type the app must support."},
        ],
        available_to_users=True,
    )
    @auth.decorators.check_api({
        "permissions": ["configurations.configurations.list"],
        "recommended_roles": {
            c.ADMINISTRATION_MODE: {"admin": True, "editor": True, "viewer": True},
            c.DEFAULT_MODE: {"admin": True, "editor": True, "viewer": True},
        },
    })
    def get(self, project_id: int, **kwargs):
        toolkit_type = request.args.get('type')
        if toolkit_type and toolkit_type not in OAUTH_APP_TOOLKIT_TYPES:
            return {"error": f"Unsupported credential type: {toolkit_type}"}, 400
        rows = list_available_apps(project_id, toolkit_type)
        return {"rows": rows, "total": len(rows), "mode": credential_mode(rows)}, 200
