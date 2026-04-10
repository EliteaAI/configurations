from copy import deepcopy
from typing import Optional

from pylon.core.tools import web

from ..local_tools import log
from ..models.pd.configuration import ConfigurationUpdateRpc
from ..models.pd.registry import CONFIG_TYPE_REGISTRY, register_config_type
from pydantic import BaseModel
from ..utils import update_configuration, create_if_not_exists, expand_configuration


class RPC:
    @web.rpc('configurations_register')
    def configurations_register(self,
        type_name: str, section: str, model: type[BaseModel] = None, config_schema: Optional[dict] = None,
        validation_func: Optional[str] = None, check_connection_func: Optional[str] = None
    ):
        return register_config_type(
            type_name=type_name,
            section=section,
            model=model,
            validation_func=validation_func,
            config_schema=config_schema,
            check_connection_func=check_connection_func
        )
    
    @web.rpc('configurations_create_if_not_exists')
    def create_if_not_exists(self, payload: dict) -> tuple[dict, bool]:
        return create_if_not_exists(payload)


    @web.rpc('configurations_list_types')
    def list_types(self):
        return list(CONFIG_TYPE_REGISTRY.values())
    

    # @web.rpc('configurations_get_type_model')
    # def get_type_model(self, type_name: str):
    #     entry = CONFIG_TYPE_REGISTRY.get(type_name)
    #     return entry.model if entry and entry.model else None

    @web.rpc('configurations_update', 'update_configuration_rpc')
    def update_configuration_rpc(self, project_id: int, config_id: int, payload: dict) -> dict:
        parsed = ConfigurationUpdateRpc.model_validate(payload)
        update_payload = parsed.model_dump(exclude_unset=True)
        return update_configuration(project_id, config_id, update_payload=update_payload)

    @web.rpc('configurations_expand')
    def configurations_expand(
        self, project_id: int, settings: dict,
        user_id: int, unsecret: bool
    ) -> dict:
        settings_expanded = deepcopy(settings)

        expand_configuration(
            payload=settings_expanded,
            current_project_id=project_id,
            user_id=user_id,
            unsecret=unsecret
        )

        return settings_expanded
