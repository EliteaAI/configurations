from tools import api_tools

from ...local_tools import APIBase, auth, config as c, log, register_openapi, rpc_manager

CATALOG_RPC_TIMEOUT_SECONDS = 12


def llm_model_catalog() -> dict:
    try:
        catalog = rpc_manager.timeout(CATALOG_RPC_TIMEOUT_SECONDS).litellm_llm_model_catalog()
    except Exception as error:  # pylint: disable=W0718
        log.warning('LLM model catalog is unavailable: %s', error)
        catalog = None
    if not isinstance(catalog, dict):
        catalog = {}
    prefixes, models = catalog.get('prefixes'), catalog.get('models')
    return {
        'prefixes': prefixes if isinstance(prefixes, dict) else {},
        'models': models if isinstance(models, dict) else {},
    }


class API(APIBase):
    url_params = [
        '<int:project_id>',
    ]

    @register_openapi(
        name="Get LLM Model Catalog",
        description=(
            "Chat models the LLM gateway's LiteLLM catalog describes, keyed by exact catalog name, with the "
            "reasoning and vision settings the LLM model form offers for each, and the catalog prefix of every "
            "credential type. Empty when the gateway cannot be reached."
        ),
        mcp_tool=False,
        parameters=[
            {"name": "project_id", "in": "path", "schema": {"type": "integer"},
             "description": "Project identifier."},
        ],
        available_to_users=False,
    )
    @auth.decorators.check_api({
        "permissions": ["configurations.configurations.list"],
        "recommended_roles": {
            c.ADMINISTRATION_MODE: {"admin": True, "editor": True, "viewer": True},
            c.DEFAULT_MODE: {"admin": True, "editor": True, "viewer": True},
        },
    })
    @api_tools.endpoint_metrics
    def get(self, project_id: int, **kwargs):
        return llm_model_catalog(), 200
