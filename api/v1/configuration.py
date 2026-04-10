from flask import request

from ...common_utils import get_public_project_id
from ...exceptions import ConfigurationError
from ...local_tools import db, APIBase, event_manager, log, auth, config as c
from ...models.configuration import Configuration
from ...models.pd.configuration import ConfigurationDetails, ConfigurationUpdate
from ...utils import update_configuration, get_options_for_nested_fields


class API(APIBase):
    url_params = [
        '<int:project_id>/<int:config_id>'
    ]

    def get(self, project_id: int, config_id: int, **kwargs):
        public_project_id = get_public_project_id()

        with db.get_session(project_id) as session:
            config = session.query(Configuration).filter_by(id=config_id).first()
            if not config:
                return {"error": "Configuration not found"}, 404

            config_data = ConfigurationDetails.model_validate(config).model_dump(mode='json')

            config_data["options"] = get_options_for_nested_fields(
                project_id, public_project_id, config.type, True  # Include shared configs
            )

            return config_data, 200

    @auth.decorators.check_api(
        {
            "permissions": ["configurations.configuration.update"],
            "recommended_roles": {
                c.ADMINISTRATION_MODE: {"admin": True, "editor": True, "viewer": False},
                c.DEFAULT_MODE: {"admin": True, "editor": True, "viewer": False},
            },
        }
    )
    def put(self, project_id: int, config_id: int, **kwargs):
        try:
            parsed = ConfigurationUpdate.model_validate(request.json)
        except Exception as e:
            return {"error": str(e)}, 400
        update_payload = parsed.model_dump(exclude_unset=True)
        try:
            updated_config = update_configuration(project_id, config_id, update_payload=update_payload)
        except ConfigurationError as ce:
            log.warning(f"Configuration error on update in field '{ce.field}': {ce.message}")
            return ce.to_dict(), 400
        except Exception:
            log.exception("Unexpected error on update during configuration creation")
            return {
                "error": f"Unexpected error",
                "field": "unknown"
            }, 500
        if not updated_config:
            return {"error": "Configuration not found"}, 404
        return ConfigurationDetails.model_validate(updated_config).model_dump(mode='json'), 200

    @auth.decorators.check_api(
        {
            "permissions": ["configurations.configuration.delete"],
            "recommended_roles": {
                c.ADMINISTRATION_MODE: {"admin": True, "editor": True, "viewer": False},
                c.DEFAULT_MODE: {"admin": True, "editor": True, "viewer": False},
            },
        }
    )
    def delete(self, project_id: int, config_id: int, **kwargs):
        with db.get_session(project_id) as session:
            config = session.query(Configuration).filter_by(id=config_id).first()
            if not config:
                return {"error": "Configuration not found"}, 404
            #
            data = ConfigurationDetails.model_validate(config).model_dump(mode='json')
            event_manager.fire_event('configuration_deleted', data)
            #
            session.delete(config)
            session.commit()
            return {"result": "deleted"}, 204
