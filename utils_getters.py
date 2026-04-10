from typing import Optional

from .common_utils import get_public_project_id, get_personal_project_id
from .local_tools import db
from .models.configuration import Configuration
from .models.pd.configuration import ConfigurationDetails


def get_project_configurations(project_id: int, filter_fields: Optional[dict] = None) -> list[dict]:
    """Retrieve configurations for a specific project from the database.

    Args:
        project_id (int): The ID of the project to fetch configurations for
        filter_fields (Optional[dict], optional): Dictionary of fields to filter configurations by.
            Will be passed directly to SQLAlchemy's filter_by. Defaults to None.

    Returns:
        list[dict]: List of configurations as JSON-compatible dictionaries.
            Each configuration is validated and serialized through ConfigurationDetails model.
    """
    if filter_fields is None:
        filter_fields = {}
    with db.get_session(project_id) as session:
        configs = session.query(Configuration).filter(
            Configuration.project_id == project_id
        ).filter_by(**filter_fields).all()
        return [
            ConfigurationDetails.model_validate(i).model_dump(mode='json')
            for i in configs
        ]


def get_all_project_configurations(project_id: int, filter_fields: Optional[dict] = None) -> list[dict]:
    """Get all configurations for a project, including shared configurations from the public project.

    Args:
        project_id (int): The ID of the project to fetch configurations for
        filter_fields (Optional[dict], optional): Dictionary of fields to filter configurations by. Defaults to None.

    Returns:
        list[dict]: Combined list of project-specific configurations and shared public configurations.
            All configurations are validated and serialized through ConfigurationDetails model.
    """
    result = get_project_configurations(project_id, filter_fields)
    #
    try:
        public_project_id = get_public_project_id()
        if project_id != public_project_id:
            new_filters = filter_fields.copy() if filter_fields else dict()
            new_filters.update({'shared': True, 'project_id': public_project_id})
            result.extend(get_project_configurations(public_project_id, filter_fields=new_filters))
    except:  # pylint: disable=W0702
        pass  # allow to create public project in ready callback(s) - fix dep loop
    #
    return result


def get_user_configurations(user_id: int, include_shared: bool = False,
                            filter_fields: Optional[dict] = None) -> list[dict]:
    """Retrieve configurations for a specific user, optionally including shared configurations.

    Args:
        user_id (int): The ID of the user to fetch configurations for
        include_shared (bool, optional): Whether to include shared configurations from public project. Defaults to False.
        filter_fields (Optional[dict], optional): Dictionary of fields to filter configurations by. Defaults to None.

    Returns:
        list[dict]: List of user's personal configurations and optionally shared configurations.
            All configurations are validated and serialized through ConfigurationDetails model.
    """
    user_project_id = get_personal_project_id(user_id)
    if include_shared:
        return get_all_project_configurations(user_project_id, filter_fields)
    else:
        return get_project_configurations(user_project_id, filter_fields)


def get_project_configuration(project_id: int, filter_fields: Optional[dict] = None) -> dict | None:
    """Retrieve configuration for a specific project from the database.

    Args:
        project_id (int): The ID of the project to fetch configurations for
        filter_fields (Optional[dict], optional): Dictionary of fields to filter configurations by. Defaults to None.

    Returns:
        list[dict]: configuration as a JSON-compatible dictionary serialized through ConfigurationDetails model.
    """
    with db.get_session(project_id) as session:
        config: Configuration | None = session.query(Configuration).filter(
            Configuration.project_id == project_id
        ).filter_by(**filter_fields).first()
        return ConfigurationDetails.model_validate(config).model_dump(mode='json') if config else None
