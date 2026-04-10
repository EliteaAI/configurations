# Repository Instructions for Configurations Plugin

## Project Overview

This is the configurations plugin of a larger Python-based conversational AI platform. The configurations plugin handles centralized configuration management, credential storage, provider integrations etc. It's built using Flask, SQLAlchemy with a PostgreSQL database, and provides a registry system for different configuration types across the platform.

## Repository Structure

The configurations plugin follows a modular microservices architecture:

- `/api/v1/` - REST API endpoints for frontend integration
- `/events/` - Event handlers for configuration lifecycle
- `/models/` - SQLAlchemy database models
- `/models/pd/` - Pydantic validation models for API requests/responses
- `/rpc/` - RPC methods for backend service communication  

## Technology Stack

- **Backend**: Python 3.11+, Flask, SQLAlchemy
- **Database**: PostgreSQL with JSONB for flexible data storage
- **Validation**: Pydantic v2 for request/response validation
- **Authentication**: Custom auth decorators with role-based permissions
- **Secret Management**: Vault integration for secure credential storage
- **Registry System**: Dynamic configuration type registration

## Database Conventions

All models use SQLAlchemy ORM with these patterns:
- JSONB `data` column for configuration settings and credentials
- JSONB `meta` column for additional metadata storage
- Session management: `with db.get_session(project_id) as session:`
- Foreign key relationships with proper constraints and indexes
- UUID fields for external sharing and identification
- Status tracking with `status_ok` and `status_logs` fields

## API Patterns

### RPC Methods
Use `@web.rpc('method_name', 'short_name')` decorators with this structure:
```python
@web.rpc('configurations_method_name', 'method_name')
def method_name(self, project_id: int, **kwargs) -> Dict[str, Any]:
    with db.get_session(project_id) as session:
        try:
            # business logic
            session.commit()
            return result
        except Exception as e:
            session.rollback()
            log.error(f"Error: {str(e)}")
            raise Exception(f"User message: {str(e)}")
```

### REST API Endpoints
Use `APIBase` base class:
```python
class API(APIBase):
    url_params = ['<int:project_id>/<int:config_id>']
    
    def get(self, project_id: int, config_id: int, **kwargs):
        # implementation
```

## Validation and Error Handling

### Pydantic Models
Use Pydantic v2 with `@field_validator`:
```python
@field_validator('field_name', mode='after')
@classmethod
def validate_field(cls, v):
    # validation logic
    return v
```

### Configuration Registry
Register new configuration types using the registry system:
```python
register_config_type(
    type_name="provider_name",
    section="llm", 
    model=ProviderModel,
    config_schema=schema_dict,
    validation_func="optional_validation_function",
    check_connection_func="optional_connection_check"
)
```

### Error Handling
Always include proper error handling with `ConfigurationError` exceptions and logging using `from pylon.core.tools import log`.

## Build and Development

### Testing
- Unit tests are in `/tests/` directory
- Run tests with pytest
- Integration tests require database setup and vault configuration

### Database Migrations
- Models are defined in `/models/` directory
- Use SQLAlchemy migrations for schema changes
- JSONB data fields provide flexibility without migrations

## File Organization Rules

- RPC methods always go in `/rpc/` folders
- Database models in `/models/` folders  
- API validation models in `/models/pd/` folders
- REST endpoints in `/api/v1/` folders
- Utility functions and business logic in root directory files
- Event handlers in `/events/` folders

## Coding Standards

- Use descriptive variable and function names
- Include comprehensive docstrings and type hints
- Follow existing import patterns with relative imports
- Store sensitive data using `SecretStr` and vault integration
- Maintain backward compatibility with existing configurations
- Handle configuration type validation gracefully

## Key Dependencies

- `sqlalchemy` - Database ORM
- `pydantic` - Data validation
- `flask` - Web framework
- `pylon.core.tools` - Internal framework tools
- Vault client for secret management

## Special Considerations

### Configuration Management
- Configurations support project-level and shared (public) access
- Nested configurations support
- Dynamic schema validation based on registered configuration types
- Automatic credential encryption and secure storage
- Status tracking for connection validation

### Registry System
- Dynamic registration of configuration types from other plugins
- Schema-based validation with custom validation functions
- Support for nested field options and references
- Connection testing capabilities for provider configurations

### Secret Management
- Automatic detection of password fields in schemas
- Integration with vault for secure credential storage
- `SecretStr` handling in Pydantic models with custom serializers
- Secure credential purging on configuration deletion

### Validation Patterns
- `elitea_title` must be alphanumeric with underscores only (max 128 chars)
- Type validation against registered configuration schemas
- Custom validation functions for complex configuration requirements
- Field-level validation with detailed error reporting

### Security
- Role-based permissions for configuration management
- Secure handling of sensitive configuration data
- Vault integration for credential encryption
- Configuration sharing controls

All API responses should include proper HTTP status codes and error messages. Authentication is role-based with different permissions for admin/editor/viewer roles. Always use `ConfigurationError` for domain-specific exceptions with field-level error details.
