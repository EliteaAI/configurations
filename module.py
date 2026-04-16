from pylon.core.tools import log
from pylon.core.tools import module

from tools import this, openapi_registry

from .models.pd.registry import clear_config_type_registry
from .environment_settings_seed import ensure_default_environment_settings
from .service_prompt_seed import ensure_default_service_prompts

class Module(module.ModuleModel):
    """ Configurations plugin module """
    def __init__(self, context, descriptor):
        self.context = context
        self.descriptor = descriptor

    def init(self):
        log.info('Initializing configurations plugin')
        self.descriptor.init_all()

    def ready(self):
        log.info('Ensuring default service prompts are present')
        try:
            ensure_default_service_prompts()
        except Exception as e:
            log.warning(f'Failed to ensure default service prompts: {e}')

        log.info('Ensuring default environment settings are present')
        try:
            ensure_default_environment_settings()
        except Exception as e:
            log.warning(f'Failed to ensure default environment settings: {e}')

        self._register_openapi()
        self._register_admin_tasks()

    def _register_openapi(self):
        """Register API endpoints with OpenAPI registry."""
        from .api import v2 as api_v2
        openapi_registry.register_plugin(
            plugin_name="configurations",
            version=self.descriptor.metadata.get("version", "1.0.0"),
            description="Configuration management for integrations, LLM models, and services",
            api_module=api_v2,
        )

    @staticmethod
    def _wrap_admin_task(method_cls, method_name, module_instance):
        """Create a plain function wrapper that preserves the original docstring.

        Pylon wraps @web.method() as functools.partial(func, module).
        The admin UI unwraps partials via .args[0] which lands on the
        module instance instead of the function, losing the docstring.
        """
        original = getattr(method_cls, method_name)

        def wrapper(*args, **kwargs):
            return original(module_instance, *args, **kwargs)

        wrapper.__doc__ = original.__doc__
        wrapper.__name__ = method_name
        return wrapper

    def _register_admin_tasks(self):
        try:
            from .methods.admin_tasks import Method  # pylint: disable=C0415
            task = self._wrap_admin_task(Method, "migrate_configuration_data_alita_title", self)
            this.for_module("admin").module.register_admin_task(
                "migrate_configuration_data_alita_title", task
            )
        except Exception as e:
            log.exception("Failed to register admin tasks: %s", e)

    def _unregister_admin_tasks(self):
        try:
            this.for_module("admin").module.unregister_admin_task(
                "migrate_configuration_data_alita_title", self.migrate_configuration_data_alita_title
            )
        except Exception as e:
            log.exception("Failed to unregister admin tasks: %s", e)

    def deinit(self):
        log.info('De-initializing configurations plugin')
        self._unregister_admin_tasks()
        clear_config_type_registry()
