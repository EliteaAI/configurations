from ...local_tools import db, APIBase, serialize, log, register_openapi
from ...models.configuration import Configuration


class API(APIBase):
    url_params = [
        '<int:project_id>'
    ]

    @register_openapi(
        name="List Configuration Types",
        description="List distinct configuration types for a project, filtered by section.",
        mcp_description="Use this tool when you need a compact list of configuration type names already used in the project, such as github, llm_model, service_prompt, or other registered types. Do not use this tool to retrieve schemas — use List Available Configuration Types. Do not use to browse full configuration records — use List Configurations. This is the most lightweight endpoint in the configurations area and is useful for quick filtering, summaries, or type presence checks.",
        parameters=[
            {"name": "project_id", "in": "path", "schema": {"type": "integer"},
             "description": "Project identifier."},
            {"name": "section", "in": "query", "schema": {"type": "string", "default": "credentials"},
             "description": "Filter by configuration section."},
        ],
        available_to_users=True,
    )
    def get(self, project_id: int, **kwargs):
        try:
            filter_section = kwargs.get('section', 'credentials')

            with db.get_session(project_id) as session:
                q = session.query(Configuration.type).distinct()

                if filter_section:
                    q = q.filter(Configuration.section == filter_section)

                tool_types = {tool_type[0] for tool_type in q.all()}
                return {"rows": serialize(tool_types), "total": len(tool_types)}
        except Exception as e:
            log.error(str(e))
            return {
                "ok": False,
                "error": "Failed to list toolkit types"
            }, 400
