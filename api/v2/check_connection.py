from copy import deepcopy
from typing import Any

from flask import request
from pydantic import RootModel, ConfigDict

from ...local_tools import APIBase, register_openapi
from ...models.pd.registry import CONFIG_TYPE_REGISTRY
from ...utils import expand_configuration
from ...local_tools import VaultClient

from tools import auth
from pylon.core.tools import log


class CheckConnectionPayload(RootModel[dict[str, Any]]):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "name": "gpt-5.1",
                    "ai_credentials": {
                        "elitea_title": "azure_creds_public",
                        "private": False
                    },
                    "context_window": 400000,
                    "max_output_tokens": 128000
                }
            ]
        }
    )


class API(APIBase):
    url_params = [
        '<int:project_id>/<string:config_type>'
    ]

    @register_openapi(
        name="Check Configuration Connection",
        description="Validate connection for a configuration payload by configuration type.",
        parameters=[
            {"name": "project_id", "in": "path", "schema": {"type": "integer"},
             "description": "Project identifier."},
            {"name": "config_type", "in": "path", "schema": {"type": "string"},
             "description": "Configuration type to validate."},
        ],
        request_body=CheckConnectionPayload,
    )
    def post(self, project_id: int, config_type: str, **kwargs):
        model = CONFIG_TYPE_REGISTRY.get(config_type)
        if not model:
            return {"success": False, "message": f"Unknown configuration type: {config_type}"}, 404
        data = deepcopy(request.json)

        vc = VaultClient(project_id)
        data = vc.unsecret(data)
        try:
            expand_configuration(
                payload=data,
                current_project_id=project_id,
                user_id=auth.current_user().get('id'),
                unsecret=True
            )
            result = model.check_connection(data)
            if result is None:
                return {"success": True}, 200
            elif isinstance(result, dict) and result.get('requires_authorization'):
                # Worker caught McpAuthorizationRequired and returned structured dict
                return {
                    "success": False,
                    "requires_authorization": True,
                    "auth_metadata": result.get('auth_metadata', {}),
                    "message": result.get('error', 'Authorization required'),
                }, 401
            elif isinstance(result, dict):
                # Extended response with additional data (e.g., discovered tools)
                return {"success": True, **result}, 200
            else:
                return {"success": False, "message": result}, 400
        except LookupError as e:
            return {"success": False, "message": str(e)}, 400
        except Exception as e:
            log.error(f"Error checking connection for {config_type}: {str(e)}")
            return {"success": False, "message": "Failed to check connection"}, 400
