# Configurations Plugin

This plugin provides RESTful APIs and RPC functions for managing configuration objects within a project. It is designed to work with Flask and SQLAlchemy, using Pydantic for data validation.

## API Endpoints

### 1. List Configurations
**GET** `/api/v2/configurations/<int:project_id>`

Query Parameters:
- `type` (optional): Filter by configuration type
- `section` (optional): Filter by configuration section
- `offset` (optional, default=0): Pagination offset
- `limit` (optional, default=20): Pagination limit

**Response:**
```json
{
  "total": 2,
  "items": [
    {
      "id": 1,
      "title": "Config A",
      "type": "general",
      "section": "main",
      ...
    },
    {
      "id": 2,
      "title": "Config B",
      "type": "advanced",
      "section": "secondary",
      ...
    }
  ],
  "offset": 0,
  "limit": 20
}
```

### 2. Create Configuration
**POST** `/api/v2/configurations/<int:project_id>`

**Request Body:**
```json
{
  "title": "New Config",
  "type": "general",
  "section": "main",
  ...
}
```

**Response (Success):**
```json
{
  "id": 3,
  "title": "New Config",
  ...
}
```

**Response (Validation Error):**
```json
{
  "error": "Validation error",
  "details": [ ... ]
}
```

**Response (Integrity Error):**
```json
{
  "error": "Configuration with title=\"New Config\" already exists",
  "traceback": "..."
}
```

## RPC Functions

- `create_configuration(data: dict)`: Creates a new configuration object. Used internally by the POST endpoint.
- Other utility functions (e.g., `store_secrets`, `purge_secrets`, `log`) are available for advanced operations and security management.

## Example Usage

### List Configurations (Python requests)
```python
import requests
resp = requests.get('http://localhost:5000/api/v2/configurations/1?type=general')
print(resp.json())
```

### Create Configuration (Python requests)
```python
import requests
payload = {
    "title": "My Config",
    "type": "general",
    "section": "main"
}
resp = requests.post('http://localhost:5000/api/v2/configurations/1', json=payload)
print(resp.json())
```

## Error Handling
- Validation errors return HTTP 422 with details.
- Integrity errors (e.g., duplicate title) return HTTP 400 with error and traceback.

## Notes
- All endpoints require a valid `project_id`.
- The author is automatically set from the current user context.

---
For more details, see the source code in `plugins/configurations/api/v2/configurations.py`.

## Auto as a creation default

For an enabled project, `POST /api/v2/configurations/models/<project_id>` accepts
`{"section":"llm","mode":"auto"}`. The project-local
`default_llm_selection_mode` Vault value records this intent separately from a
concrete model. If the project currently inherits its concrete default, enabling
Auto snapshots the effective concrete name and owning project. Selecting a
concrete LLM default clears the Auto preference. High/low-tier defaults remain
concrete and do not change this preference.

The models response exposes effective `default_selection` only while both Auto
gates allow it. Existing concrete default fields and the default-model RPC remain
concrete. New chat/ordinary-Agent creation opts in using the RPC `surface` argument;
Pipelines and internal helpers use the retained concrete model. Auto preference
is not inherited from Public, and changing defaults does not rewrite saved
chat/Agent selections. No new model configuration or database table is created.

Focused default checks: `python -m pytest tests/unit/test_model_defaults.py tests/unit/test_auto_routing.py -q`.
