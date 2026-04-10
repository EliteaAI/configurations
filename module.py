from pylon.core.tools import log
from pylon.core.tools import module
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

    def _register_openapi(self):
        """Register API endpoints with OpenAPI registry."""
        from tools import openapi_registry

        from .api import v2 as api_v2
        openapi_registry.register_plugin(
            plugin_name="configurations",
            version=self.descriptor.metadata.get("version", "1.0.0"),
            description="Configuration management for integrations, LLM models, and services",
            api_module=api_v2,
        )

    def deinit(self):
        log.info('De-initializing configurations plugin')
        clear_config_type_registry()
