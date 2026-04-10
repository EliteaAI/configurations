from typing import Optional

from pylon.core.tools import web

from ..common_utils import get_public_project_id
from ..local_tools import db, log
from ..models.configuration import Configuration
from ..models.pd.llm_model import LlmModelList
from ..utils import get_configuration_llm_models_with_limits_query, get_configurations
from ..utils_getters import (
    get_user_configurations, get_all_project_configurations,
    get_project_configurations, get_project_configuration
)
from ..utils_models import ModelConfigurationService


class RPC:
    @web.rpc('configurations_get_filtered_personal')
    def configurations_get_filtered_personal(self, user_id, include_shared: bool = False,
                                             filter_fields: Optional[dict] = None) -> list[dict]:
        """RPC endpoint to get filtered configurations for a user.

        Args:
            user_id (int): The ID of the user to fetch configurations for
            include_shared (bool, optional): Whether to include shared configurations. Defaults to False.
            filter_fields (Optional[dict], optional): Dictionary of fields to filter configurations by. Defaults to None.

        Returns:
            list[dict]: Filtered list of configurations as JSON-compatible dictionaries
        """
        return get_user_configurations(user_id, include_shared, filter_fields)

    @web.rpc('configurations_get_filtered_project')
    def configurations_get_filtered_project(self, project_id: int, include_shared: bool = False,
                                            filter_fields: Optional[dict] = None) -> list[dict]:
        """RPC endpoint to get filtered configurations for a project."""
        log.debug(f'configurations_get_filtered_project, {project_id=} {include_shared=} {filter_fields=}')
        if include_shared:
            return get_all_project_configurations(project_id, filter_fields)
        else:
            return get_project_configurations(project_id, filter_fields)

    @web.rpc('configurations_get_first_filtered_project')
    def configurations_get_first_filtered_project(self, project_id: int, filter_fields: Optional[dict] = None) -> dict | None:
        """RPC endpoint to get filtered configuration for a project."""
        log.debug(f'configurations_get_first_filtered_project, {project_id=} {filter_fields=}')
        return get_project_configuration(project_id, filter_fields)

    @web.rpc('configurations_get_filtered_public')
    def configurations_get_filtered_public(self, filter_fields: Optional[dict] = None) -> list[dict]:
        log.debug(f'configurations_get_filtered_public, {filter_fields=}')
        public_project_id = get_public_project_id()
        filter_fields = filter_fields or dict()
        filter_fields['project_id'] = public_project_id
        return get_project_configurations(project_id=public_project_id, filter_fields=filter_fields)

    @web.rpc('configurations_get_configuration_model')
    def configurations_get_configuration_model(self, project_id: int, model_name: str) -> dict:
        log.debug(f'configurations_get_configuration_model, {project_id=} {model_name=}')

        with db.get_session(project_id) as session:
            model_filters = [Configuration.data["name"].astext == model_name]

            configuration_model = get_configuration_llm_models_with_limits_query(
                session, project_id, model_filters, section='llm'
            ).first()

            if not configuration_model:
                return {}

        return LlmModelList.model_validate(configuration_model).model_dump(mode='json')

    @web.rpc('configurations_get_available_models')
    def configurations_get_available_models(
            self, project_id: int, section: str = 'llm', include_shared: bool = True
    ) -> dict:
        log.debug(f'configurations_get_available_models, {project_id=}')

        service = ModelConfigurationService(project_id)
        return service.get_available_models(section, include_shared)

    @web.rpc('configurations_get_search_options')
    def configurations_get_search_options(self, project_id: int, **kwargs) -> dict:
        log.debug(f'configurations_get_search_options, {project_id=}')

        configurations = get_configurations(
            project_id=project_id,
            type_filter=kwargs.get('type_filter'),
            section_filter=[kwargs.get('section', 'credentials')],
            offset=kwargs.get('offset', 0),
            limit=kwargs.get('limit', 10),
            include_shared=kwargs.get('include_shared', False),
            shared_offset=kwargs.get('shared_offset', 0),
            shared_limit=kwargs.get('shared_limit', 10),
            query=kwargs.get('query')
        )
        return {
            'credential': {
                'rows': [
                    {
                        'id': configuration['id'],
                        'name': configuration['label'],
                    } for configuration in configurations['items']
                ]
            }
        }

    @web.rpc('configurations_get_default_model', 'get_default_model')
    def configurations_get_default_models(
            self, project_id: int, section: str = "llm", include_shared: bool = True
    ) -> dict:
        log.debug(f'configurations_get_default_model, {project_id=}')

        service = ModelConfigurationService(project_id)
        response, _ = service.get_models(section, include_shared)
        return {
            'model_name': response['default_model_name'],
            'model_project_id': response['default_model_project_id']
        }

    @web.rpc('configurations_get_models', 'get_models')
    def configurations_get_models(
            self, project_id: int, section: str = "llm", include_shared: bool = True
    ) -> dict:
        log.debug(f'configurations_get_models, {project_id=}')

        service = ModelConfigurationService(project_id)
        response, _ = service.get_models(section, include_shared)
        return response
