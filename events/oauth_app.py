from pylon.core.tools import web, log

from ..oauth_apps import ADOPTED_EVENT, persist_adopted_app


class Event:
    @web.event(ADOPTED_EVENT)
    def oauth_app_default_adopted(self, context, event, payload: dict):
        """A credential was used with a Default app it doesn't store yet: store it (#7149, R8).

        Runs outside the read that noticed it, so agent runs and sign-ins never write.
        """
        try:
            persist_adopted_app(payload['project_id'], payload['config_id'], payload['app_id'])
        except Exception as e:  # pylint: disable=W0703
            log.warning(f"Could not store the default OAuth app on credential {payload.get('config_id')}: "
                        f"{type(e).__name__}")
