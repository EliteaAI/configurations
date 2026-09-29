from ...local_tools import APIBase, register_openapi
from ...llm_model_profiles import profiles_payload

from tools import api_tools


class API(APIBase):
    url_params = [
        '<int:project_id>',
    ]

    @register_openapi(
        name="List LLM Model Reasoning Profiles",
        description=(
            "Ordered reasoning profiles recognized from an LLM model's name, with the matching rule the "
            "form applies live and the platform flags that lock the reasoning toggle."
        ),
        mcp_tool=False,
        parameters=[
            {"name": "project_id", "in": "path", "schema": {"type": "integer"},
             "description": "Project identifier."},
        ],
        available_to_users=False,
    )
    @api_tools.endpoint_metrics
    def get(self, project_id: int, **kwargs):
        return profiles_payload(), 200
