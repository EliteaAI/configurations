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


# Where every credential row lives; a model row can never be one
CREDENTIAL_SECTION = 'ai_credentials'


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
        if include_shared:
            return get_all_project_configurations(project_id, filter_fields)
        else:
            return get_project_configurations(project_id, filter_fields)

    @web.rpc('configurations_get_first_filtered_project')
    def configurations_get_first_filtered_project(self, project_id: int, filter_fields: Optional[dict] = None) -> dict | None:
        """RPC endpoint to get filtered configuration for a project."""
        return get_project_configuration(project_id, filter_fields)

    @web.rpc('configurations_get_by_id', 'get_configuration_by_id')
    def configurations_get_by_id(self, project_id: int, configuration_id: int) -> dict | None:
        """RPC endpoint to get a single configuration by ID.

        Args:
            project_id: The project ID
            configuration_id: The configuration ID

        Returns:
            Configuration dict with id, name, type, etc. or None if not found
        """
        with db.get_session(project_id) as session:
            config = session.query(Configuration).filter_by(id=configuration_id).first()
            if config:
                return {
                    'id': config.id,
                    'name': config.label or config.elitea_title,
                    'type': config.type,
                    'section': config.section,
                }
        return None

    @web.rpc('configurations_get_filtered_public')
    def configurations_get_filtered_public(self, filter_fields: Optional[dict] = None) -> list[dict]:
        public_project_id = get_public_project_id()
        filter_fields = filter_fields or dict()
        filter_fields['project_id'] = public_project_id
        return get_project_configurations(project_id=public_project_id, filter_fields=filter_fields)

    @web.rpc('configurations_get_configuration_model')
    def configurations_get_configuration_model(self, project_id: int, model_name: str) -> dict:
        log.debug('configurations_get_configuration_model, project_id=%s model_name=%s', project_id, model_name)

        with db.get_session(project_id) as session:
            model_filters = [Configuration.data["name"].astext == model_name]

            configuration_model = get_configuration_llm_models_with_limits_query(
                session, project_id, model_filters, section='llm'
            ).first()

            if not configuration_model:
                return {}

        return LlmModelList.model_validate(configuration_model).model_dump(mode='json')

    @web.rpc('configurations_get_model_provider')
    def configurations_get_model_provider(
            self, project_id: int, model_name: str, section: str = 'llm'
    ) -> Optional[str]:
        """The credential family behind a model — the type of the credential it points at."""
        public_project_id = get_public_project_id()
        #
        provider = _lookup_model_provider(project_id, model_name, section)
        #
        if provider or not public_project_id or public_project_id == project_id:
            return provider
        #
        # Only a shared public model is reachable from another project, per expand_configuration
        return _lookup_model_provider(public_project_id, model_name, section, shared_only=True)

    @web.rpc('configurations_get_available_models')
    def configurations_get_available_models(
            self, project_id: int, section: str = 'llm', include_shared: bool = True
    ) -> dict:
        service = ModelConfigurationService(project_id)
        return service.get_available_models(section, include_shared)

    @web.rpc('configurations_get_search_options')
    def configurations_get_search_options(self, project_id: int, **kwargs) -> dict:
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
        service = ModelConfigurationService(project_id)
        response, _ = service.get_models(section, include_shared)
        return response


def _lookup_model_provider(
        project_id: int, model_name: str, section: str, shared_only: bool = False,
) -> Optional[str]:
    """The model row points at its credential by elitea_title; label collides on case."""
    filters = [
        Configuration.project_id == project_id,
        Configuration.section == section,
        Configuration.data['name'].astext == model_name,
    ]
    #
    if shared_only:
        filters.append(Configuration.shared.is_(True))
    #
    # Not .scalar(): the model name is not unique, so several rows can answer this
    with db.get_session(project_id) as session:
        references = session.query(Configuration.data['ai_credentials']).filter(*filters).all()
    #
    titles = _credential_titles(references)
    #
    if not titles:
        return None
    #
    return _single_credential_type(project_id, titles)


def _credential_titles(references) -> Optional[set]:
    """None as soon as one candidate is unresolvable here — a guessed family is worse."""
    titles = set()
    #
    for (reference,) in references:
        # private means the credential lives in its owner's personal project, not this one
        if not isinstance(reference, dict) or reference.get('private'):
            return None
        #
        title = reference.get('elitea_title')
        #
        if not title:
            return None
        #
        titles.add(title)
    #
    return titles


def _single_credential_type(project_id: int, titles: set) -> Optional[str]:
    """The family only when every candidate agrees on it; disagreement reads as unknown."""
    types = set()
    #
    with db.get_session(project_id) as session:
        for title in titles:
            row = session.query(Configuration.type).filter(
                Configuration.project_id == project_id,
                Configuration.section == CREDENTIAL_SECTION,
                Configuration.elitea_title == title,
            ).first()
            #
            types.add(row[0] if row else None)
    #
    if len(types) != 1:
        return None
    #
    return types.pop()
