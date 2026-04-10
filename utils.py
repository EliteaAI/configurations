from pydantic import SecretStr, ValidationError
from sqlalchemy import func, cast, Integer, Boolean, and_, desc, asc
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Query
from sqlalchemy import select

from .common_utils import get_personal_project_id, get_public_project_id
from .local_tools import db, store_secrets, purge_secrets, event_manager, log, VaultClient
from .models.configuration import Configuration
from .models.pd.configuration import (
    ConfigurationCreate, ConfigurationDetails, ConfigurationCreateRpc, ConfigurationList
)
from .models.pd.registry import CONFIG_TYPE_REGISTRY
from .exceptions import ConfigurationError, handle_validation_error


def _process_secret_fields(data: dict, data_properties: dict, config_type: str) -> None:
    """
    Process data fields to identify and convert secret/password fields to SecretStr.
    
    This utility function is used by both create_configuration and update_configuration
    to ensure consistent handling of secret fields.
    
    Args:
        data: Dictionary containing configuration data fields
        data_properties: Schema properties for the data fields (not the full config schema)
        config_type: Type of configuration (for error messages)
    
    Raises:
        ConfigurationError: If a field is not valid for the configuration type
    """
    for key, value in list(data.items()):
        if key in data_properties:
            key_properties = data_properties[key]
            is_password = False

            # Check if field is marked as password/secret
            if key_properties.get('format') == 'password':
                is_password = True
            elif 'anyOf' in key_properties:
                for schema_option in key_properties['anyOf']:
                    if schema_option.get('format') == 'password':
                        is_password = True
                        break

            # Convert to SecretStr if it's a password field and not already a secret reference
            if is_password and value and not (isinstance(value, str) and value.startswith('{{secret.') and value.endswith('}}')):
                data[key] = SecretStr(value)
        else:
            raise ConfigurationError(key, f"Property '{key}' is not valid for configuration type '{config_type}'")


def create_configuration(payload: dict) -> Configuration:
    """
    Create a new configuration entity in the database from payload.
    Returns the created Configuration instance.
    """
    try:
        parsed = ConfigurationCreate.model_validate(payload)
    except ValidationError as ve:
        raise handle_validation_error(ve)
    except ValueError as e:
        raise ConfigurationError("payload", str(e))

    if not parsed._entry.model:
        # Extract data properties from the full config schema
        data_properties = parsed._entry.config_schema["properties"]
        _process_secret_fields(parsed.data, data_properties, parsed.type)

    created_secrets = store_secrets(parsed)
    with db.get_session(parsed.project_id) as session:
        config = parsed.make_db_model()
        session.add(config)
        try:
            session.commit()
            session.refresh(config)
            result = ConfigurationDetails.model_validate(config).model_dump(mode='json')
            event_manager.fire_event('configuration_created', result)

            # If an LLM model is marked as a tier model, make it the tier default (last write wins).
            # This keeps tier-default dropdowns in sync without requiring manual selection.
            if parsed.type == 'llm_model':
                try:
                    model_name = str((parsed.data or {}).get('name') or '').strip()
                    model_project_id = int(parsed.project_id)
                    secrets_to_update = {}

                    if (parsed.data or {}).get('low_tier') is True and model_name:
                        secrets_to_update['default_llm_low_tier_model_name'] = model_name
                        secrets_to_update['default_llm_low_tier_model_project_id'] = model_project_id

                    if (parsed.data or {}).get('high_tier') is True and model_name:
                        secrets_to_update['default_llm_high_tier_model_name'] = model_name
                        secrets_to_update['default_llm_high_tier_model_project_id'] = model_project_id

                    if secrets_to_update:
                        vault_client = VaultClient.from_project(parsed.project_id)
                        secrets = vault_client.get_secrets()
                        secrets.update(secrets_to_update)
                        vault_client.set_secrets(secrets)
                except Exception as e:
                    log.warning(f"Failed to set tier defaults for created model: {e}")

            return result
        except IntegrityError as ie:
            purge_secrets(project_id=parsed.project_id, secrets_to_delete=created_secrets)
            if "elitea_title" in str(ie) or "unique" in str(ie).lower():

                raise ConfigurationError("elitea_title", f"Credential with ID '{parsed.elitea_title}' already exists")
            else:
                log.error(f"IntegrityError during configuration creation: {str(ie)}")
                raise ConfigurationError("database", f"Database error")


def create_if_not_exists(payload: dict) -> tuple[dict, bool]:
    """
    Create configuration if such title does not exist.
    Returns (config, created: bool)
    """
    from .local_tools import log
    log.info(f'create_if_not_exists called with elitea_title={payload.get("elitea_title")}')

    project_id = payload.get('project_id')
    elitea_title = payload.get('elitea_title')

    if not project_id or not elitea_title:
        raise ConfigurationError("payload", "project_id and elitea_title are required")

    # Check if configuration already exists
    with db.get_session(project_id) as session:
        existing = session.query(Configuration).filter_by(elitea_title=elitea_title).first()
        if existing:
            log.info(f'Configuration {elitea_title} already exists')
            return ConfigurationDetails.model_validate(existing).model_dump(mode='json'), False

    # Doesn't exist - create it using create_configuration which handles secrets properly
    try:
        config = create_configuration(payload)
        result = ConfigurationDetails.model_validate(config).model_dump(mode='json')
        return result, True
    except Exception:
        log.exception('create_if_not_exists failed during creation')
        raise


def update_configuration(project_id: int, config_id: int, update_payload: dict) -> dict:
    """
    Update an existing configuration entity in the database.
    Properly handles secret fields by storing them in vault.
    Returns the updated configuration as a dict.
    """

    status_changed = 'status_ok' in update_payload or 'status_logs' in update_payload
    with db.get_session(project_id) as session:
        config: Configuration = session.query(Configuration).filter_by(id=config_id).first()
        if not config:
            raise ValueError(f"Configuration with id {config_id} not found")
        
        # If data is being updated, handle secret fields
        if 'data' in update_payload:
            # Get the configuration type registry entry
            entry = CONFIG_TYPE_REGISTRY.get(config.type)
            if entry:
                # entry.config_schema is the "data" schema, so we get properties directly
                data_properties = entry.config_schema.get("properties", {})
                _process_secret_fields(update_payload['data'], data_properties, config.type)

                if config.type == 'service_prompt' and entry.model and hasattr(entry.model, 'model_validate'):
                    existing_key = str((config.data or {}).get('key') or config.elitea_title or '').strip().lower()
                    service_prompt_data = dict(update_payload.get('data') or {})
                    if 'key' not in service_prompt_data and existing_key:
                        service_prompt_data['key'] = existing_key

                    try:
                        validated = entry.model.model_validate(service_prompt_data).model_dump(mode='python')
                    except ValidationError as ve:
                        raise handle_validation_error(ve)
                    incoming_key = str(validated.get('key') or '').strip().lower()

                    if existing_key and incoming_key and incoming_key != existing_key:
                        raise ConfigurationError('key', 'Key is immutable')

                    update_payload['data'] = validated

                    # Keep elitea_title locked to the key to preserve one-record-per-key.
                    update_payload['elitea_title'] = existing_key or incoming_key
                
                # Store secrets in vault - this will convert SecretStr to {{secret.xxx}} references
                try:
                    store_secrets(update_payload['data'], project_id=project_id)
                except Exception as e:
                    log.error(f"Error storing secrets during update: {str(e)}")
                    raise ConfigurationError("data", f"Failed to store secret fields: {str(e)}")
        
        # Apply all updates to the configuration
        for key, value in update_payload.items():
            if key == 'status_logs':
                value = '{}{}\n'.format(config.status_logs if config.status_logs is not None else '', value)

            setattr(config, key, value)
        try:
            session.commit()
            session.refresh(config)

            # If an LLM model is marked as a tier model, make it the tier default (last write wins).
            # Run after commit so config data is stored even if vault update fails.
            if config.type == 'llm_model':
                try:
                    model_name = str((config.data or {}).get('name') or '').strip()
                    model_project_id = int(config.project_id)
                    secrets_to_update = {}

                    if (config.data or {}).get('low_tier') is True and model_name:
                        secrets_to_update['default_llm_low_tier_model_name'] = model_name
                        secrets_to_update['default_llm_low_tier_model_project_id'] = model_project_id

                    if (config.data or {}).get('high_tier') is True and model_name:
                        secrets_to_update['default_llm_high_tier_model_name'] = model_name
                        secrets_to_update['default_llm_high_tier_model_project_id'] = model_project_id

                    if secrets_to_update:
                        vault_client = VaultClient.from_project(project_id)
                        secrets = vault_client.get_secrets()
                        secrets.update(secrets_to_update)
                        vault_client.set_secrets(secrets)
                except Exception as e:
                    log.warning(f"Failed to set tier defaults for updated model: {e}")

            result = ConfigurationDetails.model_validate(config).model_dump(mode='json')
            if status_changed:
                event_manager.fire_event('configuration_status_changed', result)
            return result
        except IntegrityError as ie:
            elitea_title = update_payload.get('elitea_title', None)
            if "elitea_title" in str(ie) or "unique" in str(ie).lower():
                raise ConfigurationError("elitea_title",
                                         f"Credential with ID '{elitea_title}' already exists")
            else:
                log.error(f"IntegrityError during configuration update: {str(ie)}")
                raise ConfigurationError("database", "Database error")


def expand_configuration(payload: dict, current_project_id: int, user_id: int = None,
                         unsecret: bool = False, already_done: list = None) -> None:
    """
    Searches for a Configuration based on payload fields.
    If 'private' is False, searches by current_project_id and 'elitea_title'.
    If 'private' is True, searches by personal project id (derived from user_id) and 'elitea_title'.
    Raises ValueError if required fields are missing or configuration not found.
    Prevents infinite recursion by tracking already processed titles in already_done.
    """
    if already_done is None:
        already_done = []
    log.info(f'expand_configuration called with {payload=}, {current_project_id=}, {user_id=}, {already_done=}')
    title = payload.get('elitea_title')
    if title:
        if title in already_done:
            raise ValueError(f"Recursion error, please validate your configuration ID: {title}")
        try:
            public_project_id: int = get_public_project_id()
            private = payload.get('private')
            # if private is None or title is None:
            #     raise ValueError("Payload must contain 'private' and 'title' fields")
            if private:
                if user_id is None:
                    raise ValueError("user_id must be provided for personal configuration lookup")

                project_id = get_personal_project_id(user_id)
            else:
                project_id = current_project_id

            config = None

            # First, try to find in the primary project (current or personal)
            with db.get_session(project_id) as session:
                config = session.query(Configuration).filter_by(elitea_title=title).first()

            # If not found and we have a public_project_id, try to find shared configurations there
            if not config and public_project_id and project_id != public_project_id:
                with db.get_session(public_project_id) as public_session:
                    config = public_session.query(Configuration).filter_by(
                        elitea_title=title,
                        shared=True
                    ).first()

            if not config:
                raise LookupError(f"Configuration with title '{title}' not found for project_id {project_id} "
                                  f"or shared in public_project_id {public_project_id}")

            # Check if this is a private pgvector config being used in a shared project
            if config.type == 'pgvector':
                if config.project_id != public_project_id and payload.get('private') and current_project_id != config.project_id:
                    raise ValueError(
                        f"Private pgvector configuration '{title}' is not allowed within shared projects. "
                        f"Only public shared pgvector or team configurations are permitted."
                    )

            if unsecret:
                vc = VaultClient(config.project_id)
                naked_data = vc.unsecret(config.data)
                payload.update(naked_data)
            else:
                payload.update(config.data)

            payload['configuration_uuid'] = str(config.uuid)
            payload['configuration_project_id'] = config.project_id
            payload['configuration_type'] = config.type
        except Exception as e:
            log.exception("Error in expand_configuration")
            raise
        already_done.append(title)

    for k in payload.keys():
        if isinstance(payload[k], dict):
            log.debug(f'expand_configuration: {k} is dict, checking for elitea_title')
            expand_configuration(payload[k], current_project_id=current_project_id, user_id=user_id, unsecret=unsecret, already_done=already_done)

def get_configuration_llm_models_with_limits_query(session, project_id: int, filters: list, section: str = "llm") -> Query:
    """
    Build a query to retrieve configuration models with provided filters.

    Args:
        session: SQLAlchemy session object.
        project_id (int): The ID of the project to fetch configurations for.
        filters (list): List of SQLAlchemy filter conditions.
        section (str, optional): The section to filter configurations by. Defaults to "llm".

    Returns:
        list: Query of configuration models.
    """
    subquery = (
        session.query(
            Configuration.data["name"].label("name"),
            func.max(cast(Configuration.data['max_output_tokens'], Integer)).label('max_output_tokens')
        )
        .filter(
            Configuration.project_id == project_id,
            Configuration.status_ok == True,
            Configuration.section == section,
            *filters
        )
        .group_by(Configuration.data["name"])
        .subquery()
    )

    query = (
        session.query(
            Configuration.project_id,
            Configuration.shared,
            Configuration.data["name"].label("name"),
            Configuration.label.label("display_name"),
            Configuration.data['context_window'].label('context_window'),
            cast(Configuration.data['max_output_tokens'], Integer).label('max_output_tokens'),
            func.coalesce(Configuration.data["supports_vision"], 'true').cast(Boolean).label("supports_vision"),
            func.coalesce(Configuration.data["supports_reasoning"], 'false').label("supports_reasoning"),
            func.coalesce(Configuration.data["low_tier"], 'false').label("low_tier"),
            # NOTE: Fallback to legacy "mid_tier" for backward compatibility with existing configurations.
            #       Consider deprecating this fallback once all configs have migrated to "high_tier".
            func.coalesce(Configuration.data["high_tier"], Configuration.data["mid_tier"], 'false').label("high_tier"),
        )
        .distinct()
        .join(
            subquery,
            and_(
                Configuration.data["name"] == subquery.c["name"],
                cast(Configuration.data['max_output_tokens'], Integer) == subquery.c['max_output_tokens']
            )
        )
        .filter(
            Configuration.project_id == project_id,
            Configuration.status_ok == True,
            Configuration.section == section,
            *filters
        )
    )

    return query


def get_embedding_model_query(session, project_id: int, filters: list, section: str = "embedding"):
    query = (
        session.query(
            Configuration.project_id,
            Configuration.shared,
            Configuration.data["name"].label("name"),
            Configuration.label.label("display_name"),
        )
        .distinct()
        .filter(
            Configuration.project_id == project_id,
            Configuration.status_ok == True,
            Configuration.section == section,
            *filters
        )
    )
    return query


def get_vector_storage_query(session, project_id: int, filters: list, section: str = "vectorstorage"):
    query = (
        session.query(
            Configuration.project_id,
            Configuration.shared,
            Configuration.elitea_title
        )
        .distinct()
        .filter(
            Configuration.project_id == project_id,
            Configuration.status_ok == True,
            Configuration.section == section,
            *filters
        )
    )
    return query


def get_image_generation_model_query(session, project_id: int, filters: list, section: str = "image_generation"):
    """
    Build a query to retrieve image generation model configurations.
    """
    query = (
        session.query(
            Configuration.project_id,
            Configuration.shared,
            Configuration.data["name"].label("name"),
            Configuration.label.label("display_name"),
        )
        .distinct()
        .filter(
            Configuration.project_id == project_id,
            Configuration.status_ok == True,
            Configuration.section == section,
            *filters
        )
    )
    return query


def extract_nested_field_info(schema_data: dict) -> dict:
    """
    Extract information about nested configuration fields from schema.
    Returns dict with field_name -> required_types/sections mapping.
    """
    nested_fields = {}

    def traverse_schema(obj, parent_key=""):
        if isinstance(obj, dict):
            # Check for configuration_types or configuration_sections
            if "configuration_types" in obj:
                nested_fields[parent_key] = {
                    "type": "types",
                    "values": obj["configuration_types"]
                }
            elif "configuration_sections" in obj:
                nested_fields[parent_key] = {
                    "type": "sections",
                    "values": obj["configuration_sections"]
                }
            for key, value in obj.items():
                traverse_schema(value, key)
        elif isinstance(obj, list):
            for item in obj:
                traverse_schema(item, parent_key)

    traverse_schema(schema_data)
    return nested_fields

def get_options_for_nested_fields(
        project_id: int, public_project_id: int, config_type: str, include_shared: bool = False
) -> dict:
    """
    Get available configuration options for nested fields in a configuration type.
    """
    options = {}

    # Get the registry entry for this configuration type
    registry_entry = CONFIG_TYPE_REGISTRY.get(config_type)
    if not registry_entry or not registry_entry.config_schema:
        return options

    # Extract nested field information from schema
    nested_fields = extract_nested_field_info(registry_entry.config_schema)

    if not nested_fields:
        return options

    # Get all configurations from both project and shared (if requested)
    all_configs = []

    # Get project configurations
    with db.get_session(project_id) as session:
        project_configs = session.execute(
            select(Configuration).where(Configuration.project_id == project_id)
        ).scalars().all()
        all_configs.extend(project_configs)

    # Get shared configurations if requested
    if include_shared and project_id != public_project_id:
        with db.get_session(public_project_id) as public_session:
            shared_configs = public_session.execute(
                select(Configuration).where(
                    Configuration.project_id == public_project_id,
                    Configuration.shared == True
                )
            ).scalars().all()
            all_configs.extend(shared_configs)

    # Build options for each nested field
    for field_name, field_info in nested_fields.items():
        field_options = []

        if field_info["type"] == "types":
            # Filter by configuration types
            required_types = field_info["values"]
            matching_configs = [
                config for config in all_configs
                if config.type in required_types
            ]
        elif field_info["type"] == "sections":
            # Filter by configuration sections
            required_sections = field_info["values"]
            matching_configs = [
                config for config in all_configs
                if config.section in required_sections
            ]
        else:
            matching_configs = []

        # Convert matching configurations to option format
        for config in matching_configs:
            field_options.append({
                "elitea_title": config.elitea_title,
                "label": config.label,
                "type": config.type,
                "section": config.section,
                "shared": config.shared,
                "project_id": config.project_id,
            })

        options[field_name] = field_options

    return options


def get_configurations(
    project_id: int,
    type_filter: list = None,
    section_filter: list = None,
    offset: int = 0,
    limit: int = 20,
    include_shared: bool = False,
    shared_offset: int = 0,
    shared_limit: int = 20,
    query: str = None,
    sort_by: str = "created_at",
    sort_order: str = "desc"
):
    from .local_tools import rpc_manager

    public_project_id = get_public_project_id()
    response = {}

    with db.get_session(project_id) as session:
        # Import pin utility from social plugin
        try:
            add_pins_with_priority = rpc_manager.timeout(2).social_add_pins_with_priority()
            extra_columns = []
        except Exception as e:
            log.warning(f"Failed to load pin utility: {e}")
            add_pins_with_priority = None
            extra_columns = []

        # Build and execute project configurations query
        project_query = session.query(Configuration).filter(Configuration.project_id == project_id)

        if type_filter:
            project_query = project_query.filter(Configuration.type.in_(type_filter))
        if section_filter:
            project_query = project_query.filter(Configuration.section.in_(section_filter))
        if query:
            project_query = project_query.filter(Configuration.label.ilike(f"%{query}%"))

        # Add pin status (project-wide) if available
        if add_pins_with_priority:
            try:
                project_query, new_columns = add_pins_with_priority(
                    original_query=project_query,
                    project_id=project_id,
                    entity=Configuration
                )
                extra_columns.extend(new_columns)
            except Exception as e:
                log.warning(f"Failed to add pin priority: {e}")

        # Apply sorting: pinned items always first (by updated_at DESC), then regular sorting
        sort_column = getattr(Configuration, sort_by, Configuration.created_at)
        if extra_columns:
            # Pin priority sorting
            if sort_order.lower() == "asc":
                project_query = project_query.order_by(
                    desc(project_query.column_descriptions[-2]['expr']),  # is_pinned
                    desc(project_query.column_descriptions[-1]['expr']),  # pin_updated_at
                    asc(sort_column),
                    asc(Configuration.id)
                )
            else:
                project_query = project_query.order_by(
                    desc(project_query.column_descriptions[-2]['expr']),  # is_pinned
                    desc(project_query.column_descriptions[-1]['expr']),  # pin_updated_at
                    desc(sort_column),
                    asc(Configuration.id)
                )
        else:
            # Regular sorting (no pins)
            if sort_order.lower() == "asc":
                project_query = project_query.order_by(asc(sort_column))
            else:
                project_query = project_query.order_by(desc(sort_column))

        # Get total count for project configurations
        project_total = project_query.count()

        # Auto-correct offset if it exceeds total count to prevent empty results
        # This handles cases where filters are applied after pagination, ensuring users see available items
        actual_offset = offset
        if project_total > 0 and offset >= project_total:
            actual_offset = 0
            log.info(f"Auto-corrected offset from {offset} to 0 (total={project_total}) to show available items")

        # Apply pagination to project query
        project_configs = project_query.offset(actual_offset).limit(limit).all()

        # Convert configurations to response format
        project_items = []
        for c in project_configs:
            # Extract configuration and pin attributes
            try:
                config, *extra_data = c
                for k, v in zip(extra_columns, extra_data):
                    setattr(config, k, v)
            except (TypeError, ValueError):
                config = c

            config_data = ConfigurationList.model_validate(config).model_dump(mode='json')

            # Add options for nested fields - use try/except to ensure loop continues even if options fetch fails
            try:
                config_data["options"] = get_options_for_nested_fields(
                    project_id, public_project_id, config.type, include_shared
                )
            except Exception as e:
                log.warning(f"Failed to get nested field options for config {config.id} (type={config.type}): {e}")
                config_data["options"] = {}

            project_items.append(config_data)

        # Add project configurations to response
        response.update({
            "total": project_total,
            "items": project_items,
            "offset": actual_offset,
            "limit": limit
        })

    # If include_shared is True and not in public project, get shared configurations
    if include_shared and project_id != public_project_id:
        # Create new session for public project schema
        with db.get_session(public_project_id) as public_session:
            shared_query = select(Configuration).where(
                Configuration.project_id == public_project_id,
                Configuration.shared == True
            )
            if type_filter:
                shared_query = shared_query.where(Configuration.type.in_(type_filter))
            if section_filter:
                shared_query = shared_query.where(Configuration.section.in_(section_filter))

            # Apply sorting to shared query
            sort_column = getattr(Configuration, sort_by, Configuration.created_at)
            if sort_order.lower() == "asc":
                shared_query = shared_query.order_by(asc(sort_column))
            else:
                shared_query = shared_query.order_by(desc(sort_column))

            # Get total count for shared configurations
            shared_total = public_session.scalar(select(func.count()).select_from(shared_query.subquery()))

            # Auto-correct shared_offset if it exceeds total count
            actual_shared_offset = shared_offset
            if shared_total > 0 and shared_offset >= shared_total:
                actual_shared_offset = 0
                log.info(f"Auto-corrected shared_offset from {shared_offset} to 0 (shared_total={shared_total}) to show available items")

            # Apply pagination to shared query
            shared_configs = public_session.execute(
                shared_query.offset(actual_shared_offset).limit(shared_limit)).scalars().all()

            # Convert shared configurations to response format
            shared_items = []
            for c in shared_configs:
                config_data = ConfigurationList.model_validate(c).model_dump(mode='json')

                # Add options for nested fields if requested - use try/except to ensure loop continues even if options fetch fails
                try:
                    config_data["options"] = get_options_for_nested_fields(
                        public_project_id, public_project_id, c.type, True
                    )
                except Exception as e:
                    log.warning(f"Failed to get nested field options for shared config {c.id} (type={c.type}): {e}")
                    config_data["options"] = {}

                shared_items.append(config_data)

            # Add shared configurations to response
            response["shared"] = {
                "total": shared_total,
                "items": shared_items,
                "offset": actual_shared_offset,
                "limit": shared_limit
            }
    return response