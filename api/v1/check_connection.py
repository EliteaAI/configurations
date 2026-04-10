from copy import deepcopy
from flask import request

from ...local_tools import APIBase
from ...models.pd.registry import CONFIG_TYPE_REGISTRY
from ...utils import expand_configuration
from ...local_tools import VaultClient

from tools import auth
from pylon.core.tools import log


class API(APIBase):
    url_params = [
        '<int:project_id>/<string:config_type>'
    ]

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
            else:
                return {"success": False, "message": result}, 400
        except LookupError as e:
            return {"success": False, "message": str(e)}, 400
        except Exception as e:
            log.error(f"Error checking connection for {config_type}: {str(e)}")
            return {"success": False, "message": "Failed to check connection"}, 400
