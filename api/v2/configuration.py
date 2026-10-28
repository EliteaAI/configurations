from flask import request

from ...common_utils import get_public_project_id
from ...exceptions import ConfigurationError
from ...local_tools import db, APIBase, log, auth, config as c, rpc_manager, register_openapi
from ...models.configuration import Configuration
from ...models.pd.configuration import ConfigurationDetails, ConfigurationUpdate
from ...models.pd.registry import ADMIN_MANAGED_TYPES, managed_configuration_error
from ...tracing_access import can_manage_stored_login, can_manage_tracing, is_tracing_type, stored_login_changed
from ...utils import update_configuration, delete_configuration, get_options_for_nested_fields
from ...folder_access import require_folder_access
from ...oauth_apps import decorate_credentials

TRACING_DENIED = {"error": "Tracing configurations are managed by project admins"}
STORED_LOGIN_DENIED = "Only project admins can log in or out on a team credential"
ADMIN_MANAGED_DENIED = {"error": "This configuration is managed in the Admin Portal"}


def deny_tracing_access(project_id: int, config_type: str | None) -> bool:
    if not is_tracing_type(config_type):
        return False
    return not can_manage_tracing(project_id, auth.current_user().get('id'))


def read_configuration(project_id: int, config_id: int) -> tuple[str | None, dict, str | None]:
    # Project-scoped because this feeds an authorization decision made against the
    # project in the URL, and ids are unique per table rather than per project
    with db.get_session(project_id) as session:
        row = session.query(Configuration.type, Configuration.data, Configuration.elitea_title).filter_by(
            id=config_id, project_id=project_id
        ).first()
    return (row[0], row[1] or {}, row[2]) if row else (None, {}, None)


def refuse_write(project_id: int, config_type: str | None, title: str | None = None) -> tuple[dict, int] | None:
    """The error answer when the stored type or the new title forbids this REST write; None when it goes on."""
    if deny_tracing_access(project_id, config_type):
        return TRACING_DENIED, 403
    if config_type in ADMIN_MANAGED_TYPES:
        return ADMIN_MANAGED_DENIED, 403
    managed = managed_configuration_error(config_type, title, project_id)
    if managed:
        return managed, 403
    return None


class API(APIBase):
    url_params = [
        '<int:project_id>/<int:config_id>'
    ]

    @register_openapi(
        name="Get Configuration",
        description="Get a single configuration by id.",
        mcp_tool=True,
        mcp_description="Use this tool when you already know the config_id of a saved configuration and need its full details for display, editing, or debugging. Do not use this tool to discover what configuration types are supported — use List Available Configuration Types. Do not use to browse all project configurations — use List Configurations. This endpoint is best for 'open this exact configuration record' workflows, especially before update actions or when nested options must be resolved.",
        parameters=[
            {"name": "project_id", "in": "path", "schema": {"type": "integer"},
             "description": "Project identifier."},
            {"name": "config_id", "in": "path", "schema": {"type": "integer"},
             "description": "Configuration identifier."},
        ],
        available_to_users=True,
    )
    @auth.decorators.check_api(
        {
            "permissions": ["configurations.configuration.details"],
            "recommended_roles": {
                c.ADMINISTRATION_MODE: {"admin": True, "editor": True, "viewer": True},
                c.DEFAULT_MODE: {"admin": True, "editor": True, "viewer": True},
            },
        }
    )
    @require_folder_access('config_id')
    def get(self, project_id: int, config_id: int, **kwargs):
        public_project_id = get_public_project_id()

        with db.get_session(project_id) as session:
            config = session.query(Configuration).filter_by(id=config_id).first()
            if not config:
                return {"error": "Configuration not found"}, 404

            # 404 rather than 403 so the credential's existence stays concealed. Gating is
            # safe here but not on the list endpoint, which the agent runtime reads as the
            # invoking user; nothing machine-facing fetches a configuration by id.
            if deny_tracing_access(project_id, config.type):
                return {"error": "Configuration not found"}, 404
            # OAuth apps are read through the Admin Portal API, which never returns the secret
            if config.type in ADMIN_MANAGED_TYPES:
                return {"error": "Configuration not found"}, 404

            is_pinned = rpc_manager.timeout(2).social_is_pinned(
                project_id=project_id, entity='configuration', entity_id=config_id
            )
            setattr(config, 'is_pinned', is_pinned)

            config_data = ConfigurationDetails.model_validate(config).model_dump(mode='json')
            decorate_credentials([config_data], project_id)

            config_data["options"] = get_options_for_nested_fields(
                project_id, public_project_id, config.type, True  # Include shared configs
            )

            return config_data, 200

    @register_openapi(
        name="Update Configuration",
        description="Update an existing configuration by id.",
        mcp_tool=True,
        mcp_description="Use this tool when you need to edit an existing configuration record, such as changing model properties, updating credentials, toggling shared, or adjusting metadata. Do not use this tool to create a new configuration — use Create Configuration. Do not use to change the default model for a section — use Set Default Model. Treat this as the main 'edit saved configuration instance' tool. It is especially appropriate when the caller already has a config_id and needs validation-aware updates with secret handling.",
        parameters=[
            {"name": "project_id", "in": "path", "schema": {"type": "integer"},
             "description": "Project identifier."},
            {"name": "config_id", "in": "path", "schema": {"type": "integer"},
             "description": "Configuration identifier."},
        ],
        request_body=ConfigurationUpdate,
        available_to_users=True,
    )
    @auth.decorators.check_api(
        {
            "permissions": ["configurations.configuration.update"],
            "recommended_roles": {
                c.ADMINISTRATION_MODE: {"admin": True, "editor": True, "viewer": False},
                c.DEFAULT_MODE: {"admin": True, "editor": True, "viewer": False},
            },
        }
    )
    @require_folder_access('config_id', write=True)
    def put(self, project_id: int, config_id: int, **kwargs):
        # The stored type is authoritative - ConfigurationUpdate cannot carry one
        config_type, stored_data, stored_title = read_configuration(project_id, config_id)
        try:
            parsed = ConfigurationUpdate.model_validate(request.json)
        except Exception as e:
            return {"error": str(e)}, 400
        update_payload = parsed.model_dump(exclude_unset=True)
        new_title = update_payload.get('elitea_title')
        # A record that already carries a reserved title stays editable; only a change to one is refused
        refused = refuse_write(project_id, config_type, new_title if new_title != stored_title else None)
        if refused:
            return refused
        if 'data' in update_payload and stored_login_changed(stored_data, update_payload['data']):
            if not can_manage_stored_login(project_id, auth.current_user().get('id')):
                return {"error": STORED_LOGIN_DENIED, "field": "refresh_token"}, 403
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

    @register_openapi(
        name="Delete Configuration",
        description="Delete a configuration by id.",
        parameters=[
            {"name": "project_id", "in": "path", "schema": {"type": "integer"},
             "description": "Project identifier."},
            {"name": "config_id", "in": "path", "schema": {"type": "integer"},
             "description": "Configuration identifier."},
        ],
        available_to_users=True,
    )
    @auth.decorators.check_api(
        {
            "permissions": ["configurations.configuration.delete"],
            "recommended_roles": {
                c.ADMINISTRATION_MODE: {"admin": True, "editor": True, "viewer": False},
                c.DEFAULT_MODE: {"admin": True, "editor": True, "viewer": False},
            },
        }
    )
    @require_folder_access('config_id', write=True)
    def delete(self, project_id: int, config_id: int, **kwargs):
        # Gated too: deleting the admin's credential silently stops project tracing
        refused = refuse_write(project_id, read_configuration(project_id, config_id)[0])
        if refused:
            return refused
        try:
            result = delete_configuration(project_id, config_id)
        except ConfigurationError as ce:
            return ce.to_dict(), 400
        if result is None:
            return {"error": "Configuration not found"}, 404
        return {"result": "deleted"}, 204
