from flask import request

from ...models.pd.llm_model import SetDefaultModel
from ...local_tools import APIBase, VaultClient, register_openapi
from ...utils_models import ModelConfigurationService

from tools import api_tools
from pylon.core.tools import log



class API(APIBase):
    url_params = [
        '<int:project_id>'
    ]

    @register_openapi(
        name="List Models",
        description="List available LLM and embedding models for a project. Returns distinct models by project_id and name.",
        parameters=[
            {"name": "include_shared", "in": "query", "schema": {"type": "boolean", "default": False},
             "description": "Include shared configuration models"},
            {"name": "section", "in": "query", "schema": {"type": "string", "default": "llm"},
             "description": "Filter by section (llm, embedding)"},
        ],
    )
    @api_tools.endpoint_metrics
    def get(self, project_id: int, **kwargs):
        """
        Retrieve configuration models for a given project, with an option to include shared configurations.
        Returns only distinct items by project_id and name combination.

        Args:
            project_id (int): The ID of the project to fetch configurations for.

        Query Parameters:
            include_shared (bool, optional): Whether to include shared configuration models from the public project.
                - Defaults to False.
                - Set to True to include shared configurations.

        Returns:
            tuple: A response containing:
                - dict: A dictionary with the total count of configurations and a list of configuration items.
                    - "total": Total number of distinct configurations by project_id and name combination.
                    - "items": List of distinct configuration models.
                - int: HTTP status code (200 on success).
        """
        include_shared = request.args.get("include_shared", default="false").lower() == "true"
        section = request.args.get("section", default="llm").lower()

        service = ModelConfigurationService(project_id)
        return service.get_models(section, include_shared)

    @api_tools.endpoint_metrics
    def post(self, project_id: int, **kwargs):
        try:
            parsed = SetDefaultModel.model_validate(request.json)
        except Exception as e:
            return {"error": str(e)}, 400

        secret_key = f'default_{parsed.section}_model_name'
        secret_key_project = f'default_{parsed.section}_model_project_id'

        try:
            vault_client = VaultClient.from_project(project_id)
            secrets = vault_client.get_secrets()
            secrets[secret_key] = parsed.name
            secrets[secret_key_project] = parsed.target_project_id

            vault_client.set_secrets(secrets)
        except Exception as e:
            log.error(f"Error setting default model: {e}")
            return {"result": "error"}, 400

        return {"result": "success"}, 200
