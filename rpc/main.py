from copy import deepcopy
from typing import Optional

from pylon.core.tools import web

from ..local_tools import log
from ..models.pd.configuration import ConfigurationUpdateRpc
from ..models.pd.registry import (
    CONFIG_TYPE_REGISTRY,
    register_config_type,
    unregister_config_type,
)
from pydantic import BaseModel
from ..utils import update_configuration, create_if_not_exists, expand_configuration, delete_configuration


class RPC:
    @web.rpc('configurations_register')
    def configurations_register(self,
        type_name: str, section: str, model: type[BaseModel] = None, config_schema: Optional[dict] = None,
        validation_func: Optional[str] = None, check_connection_func: Optional[str] = None,
        replace: bool = False, managed_by: str | None = None, manage_url: str | None = None,
    ):
        return register_config_type(
            type_name=type_name,
            section=section,
            model=model,
            validation_func=validation_func,
            config_schema=config_schema,
            check_connection_func=check_connection_func,
            replace=replace,
            managed_by=managed_by,
            manage_url=manage_url,
        )

    @web.rpc('configurations_unregister')
    def configurations_unregister(self, type_name: str, owner: str | None = None) -> bool:
        return unregister_config_type(type_name, owner)
    
    @web.rpc('configurations_create_if_not_exists')
    def create_if_not_exists(self, payload: dict, owner: str | None = None) -> tuple[dict, bool]:
        return create_if_not_exists(payload, owner=owner)


    @web.rpc('configurations_list_types')
    def list_types(self):
        return list(CONFIG_TYPE_REGISTRY.values())
    

    # @web.rpc('configurations_get_type_model')
    # def get_type_model(self, type_name: str):
    #     entry = CONFIG_TYPE_REGISTRY.get(type_name)
    #     return entry.model if entry and entry.model else None

    @web.rpc('configurations_update', 'update_configuration_rpc')
    def update_configuration_rpc(self, project_id: int, config_id: int, payload: dict,
                                 owner: str | None = None) -> dict:
        parsed = ConfigurationUpdateRpc.model_validate(payload)
        update_payload = parsed.model_dump(exclude_unset=True)
        return update_configuration(project_id, config_id, update_payload=update_payload,
                                    owner=owner)

    @web.rpc('configurations_delete')
    def configurations_delete(self, project_id: int, config_id: int, owner: str | None = None) -> bool:
        return delete_configuration(project_id, config_id, owner=owner) is not None

    @web.rpc('configurations_count_section')
    def configurations_count_section(self, project_id: int, section: str) -> int:
        from ..local_tools import db
        from ..models.configuration import Configuration
        with db.get_session(project_id) as session:
            return session.query(Configuration).filter(
                Configuration.project_id == project_id, Configuration.section == section,
            ).count()

    @web.rpc('configurations_delete_section')
    def configurations_delete_section(self, project_id: int, section: str) -> dict:
        """ Bulk project data delete (#7242): every configuration of a section, shared ones
            included. Each goes through delete_configuration so its checks and events apply;
            plugin-managed ones are skipped, the REST delete refuses them too """
        from ..local_tools import db
        from ..models.configuration import Configuration
        from ..models.pd.registry import owned_entry
        with db.get_session(project_id) as session:
            rows = session.query(Configuration.id, Configuration.type, Configuration.elitea_title).filter(
                Configuration.project_id == project_id, Configuration.section == section,
            ).all()
        deleted, failed, skipped = 0, 0, 0
        for config_id, config_type, title in rows:
            if owned_entry(config_type, title):
                skipped += 1
                continue
            try:
                if delete_configuration(project_id, config_id) is not None:
                    deleted += 1
            except Exception:  # pylint: disable=W0703
                failed += 1
                log.exception(
                    "configurations_delete_section: failed to delete configuration %s of project %s",
                    config_id, project_id,
                )
        return {'deleted': deleted, 'failed': failed, 'skipped': skipped}

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
