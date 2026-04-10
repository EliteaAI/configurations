from ...local_tools import db, APIBase, serialize, log
from ...models.configuration import Configuration


class API(APIBase):
    url_params = [
        '<int:project_id>'
    ]

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
