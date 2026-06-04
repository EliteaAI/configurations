from copy import deepcopy
from typing import Any

from flask import request
from pydantic import BaseModel, RootModel

from ...local_tools import APIBase, VaultClient, register_openapi
from ...models.pd.registry import CONFIG_TYPE_REGISTRY
from ...utils import expand_configuration
from tools import auth
from pylon.core.tools import log


class BatchCheckItem(BaseModel):
    id: str
    type: str
    data: dict[str, Any] = {}


class BatchCheckPayload(RootModel[list[BatchCheckItem]]):
    pass


class API(APIBase):
    url_params = ['<int:project_id>']

    @register_openapi(
        name="Batch Check Configuration Connections",
        description="Validate connections for multiple configuration payloads in a single request. "
                    "Returns one result object per input item, preserving the supplied 'id' field "
                    "for client-side matching.",
        parameters=[
            {
                "name": "project_id",
                "in": "path",
                "schema": {"type": "integer"},
                "description": "Project identifier.",
            },
        ],
        request_body=BatchCheckPayload,
    )
    def post(self, project_id: int, **kwargs):
        items = request.json or []
        vc = VaultClient(project_id)
        user_id = auth.current_user().get('id')
        results = []

        for item in items:
            item_id = item.get('id')
            config_type = item.get('type')
            data = deepcopy(item.get('data') or {})

            registry_entry = CONFIG_TYPE_REGISTRY.get(config_type)
            if not registry_entry:
                results.append({'id': item_id, 'success': False, 'unsupported': True})
                continue

            data = vc.unsecret(data)
            try:
                expand_configuration(
                    payload=data,
                    current_project_id=project_id,
                    user_id=user_id,
                    unsecret=True,
                )
                result = registry_entry.check_connection(data)
                if result is None:
                    results.append({'id': item_id, 'success': True})
                elif isinstance(result, dict) and result.get('requires_authorization'):
                    results.append({
                        'id': item_id,
                        'success': False,
                        'requires_authorization': True,
                        'auth_metadata': result.get('auth_metadata', {}),
                        'message': result.get('error', 'Authorization required'),
                    })
                else:
                    results.append({
                        'id': item_id,
                        'success': False,
                        'message': result if isinstance(result, str) else str(result),
                    })
            except LookupError as e:
                results.append({'id': item_id, 'success': False, 'message': str(e)})
            except Exception as e:
                log.error(f"Error checking connection for {config_type} (id={item_id}): {str(e)}")
                results.append({'id': item_id, 'success': False, 'message': 'Failed to check connection'})

        return results, 200
