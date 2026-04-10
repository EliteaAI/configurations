from pylon.core.tools import web, log


class Event:
    @web.event('configuration_created')
    def configuration_created(self, context, event, payload: dict):
        configuration_type = payload['type']
        if configuration_type == 'pgvector':
            self.update_configuration_rpc(
                project_id=payload['project_id'],
                config_id=payload['id'],
                payload={'status_ok': True}
            )
