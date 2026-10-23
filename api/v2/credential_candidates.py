"""Credential configurations that can be registered as governed references (#7119, C-1009), administration mode.

URL: GET /api/v2/configurations/credential_candidates/administration/<project_id>
Metadata only: no configuration data, no secret field, no secret template is ever returned.
"""
from flask import request

from tools import api_tools, auth

from pylon.core.tools import log

from ...governed_probe import CREDENTIALS_SECTION
from ...local_tools import register_openapi, rpc_manager, config as c

OPENAPI_TAG = "configurations/credential_candidates"
PERMISSION = "credentials.view"
DEFAULT_LIMIT = 20
RPC_TIMEOUT_SECONDS = 5

MESSAGES = {
    "INVALID_SECTION": "Only the credentials section can be listed.",
    "INVALID_LIMIT": "limit must be a positive integer.",
    "INVALID_OFFSET": "offset must be a non-negative integer.",
    "INVALID_SEARCH": "search must be text of at most 128 characters.",
}
NOT_FOUND = ({"error": "Project not found.", "code": "NOT_FOUND"}, 404)
UNAVAILABLE = ({"error": "A required service is unavailable. Try again later.", "code": "DEPENDENCY_UNAVAILABLE"}, 503)


def _invalid(code: str):
    return {"error": MESSAGES[code], "code": code}, 400


def _int_arg(name: str, default: int, code: str):
    raw = request.args.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        raise ValueError(code) from None


class AdminAPI(api_tools.APIModeHandler):
    @register_openapi(
        name="List Credential Candidates",
        description="Credential configurations of a project that can be registered as governed credential "
                    "references. Metadata only.",
        tags=[OPENAPI_TAG],
        parameters=[
            {"name": "project_id", "in": "path", "schema": {"type": "integer"}, "description": "Project identifier."},
            {"name": "section", "in": "query", "required": False,
             "schema": {"type": "string", "default": CREDENTIALS_SECTION, "enum": [CREDENTIALS_SECTION]},
             "description": "Only the credentials section is supported."},
            {"name": "search", "in": "query", "required": False, "schema": {"type": "string", "maxLength": 128},
             "description": "Literal substring of the configuration title or label (case-insensitive)."},
            {"name": "limit", "in": "query", "required": False,
             "schema": {"type": "integer", "default": DEFAULT_LIMIT, "minimum": 1, "maximum": 100},
             "description": "Page size; values above 100 are capped at 100."},
            {"name": "offset", "in": "query", "required": False,
             "schema": {"type": "integer", "default": 0, "minimum": 0}},
        ],
        mcp_tool=False,
        available_to_users=False,
    )
    @auth.decorators.check_api({
        "permissions": [PERMISSION],
        "recommended_roles": {
            c.ADMINISTRATION_MODE: {"super_admin": True, "admin": True, "editor": False, "viewer": False},
        },
    })
    @api_tools.endpoint_metrics
    def get(self, project_id: int, **kwargs):
        # Administration mode does not scope by project, so an unknown id is refused here (fail closed).
        try:
            project = rpc_manager.timeout(RPC_TIMEOUT_SECONDS).project_get_by_id(project_id)
        except Exception as exc:  # pylint: disable=W0718
            log.warning("credential_candidates: project lookup failed: %s", type(exc).__name__)
            return UNAVAILABLE
        if not project:
            return NOT_FOUND
        try:
            return self.module.configuration_credential_list(
                project_id,
                section=request.args.get("section") or CREDENTIALS_SECTION,
                search=request.args.get("search"),
                limit=_int_arg("limit", DEFAULT_LIMIT, "INVALID_LIMIT"),
                offset=_int_arg("offset", 0, "INVALID_OFFSET"),
            ), 200
        except ValueError as exc:
            code = str(exc)
            if code not in MESSAGES:
                raise
            return _invalid(code)


class API(api_tools.APIBase):
    url_params = [
        "<string:mode>/<int:project_id>",
    ]

    mode_handlers = {
        c.ADMINISTRATION_MODE: AdminAPI,
    }
