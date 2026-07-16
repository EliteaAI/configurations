"""Unit tests for pure functions in utils.py."""
import pytest
import pathlib
import sys

TESTS_DIR = pathlib.Path(__file__).resolve().parents[1]
PLUGIN_ROOT = TESTS_DIR.parent
sys.path.insert(0, str(TESTS_DIR))


def extract_nested_field_info(schema_data: dict) -> dict:
    """Pure function copied from utils.py for testing without imports."""
    nested_fields = {}

    def traverse_schema(obj, parent_key=""):
        if isinstance(obj, dict):
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


class TestExtractNestedFieldInfo:
    """Tests for extract_nested_field_info function."""

    def test_empty_schema(self):
        result = extract_nested_field_info({})
        assert result == {}

    def test_schema_without_configuration_fields(self):
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "value": {"type": "integer"}
            }
        }
        result = extract_nested_field_info(schema)
        assert result == {}

    def test_schema_with_configuration_types(self):
        schema = {
            "type": "object",
            "properties": {
                "llm_config": {
                    "type": "string",
                    "configuration_types": ["llm_model", "llm_provider"]
                }
            }
        }
        result = extract_nested_field_info(schema)
        assert "llm_config" in result
        assert result["llm_config"]["type"] == "types"
        assert result["llm_config"]["values"] == ["llm_model", "llm_provider"]

    def test_schema_with_configuration_sections(self):
        schema = {
            "type": "object",
            "properties": {
                "storage_config": {
                    "type": "string",
                    "configuration_sections": ["vectorstorage", "embedding"]
                }
            }
        }
        result = extract_nested_field_info(schema)
        assert "storage_config" in result
        assert result["storage_config"]["type"] == "sections"
        assert result["storage_config"]["values"] == ["vectorstorage", "embedding"]

    def test_nested_schema(self):
        schema = {
            "type": "object",
            "properties": {
                "outer": {
                    "type": "object",
                    "properties": {
                        "inner_config": {
                            "configuration_types": ["embedding"]
                        }
                    }
                }
            }
        }
        result = extract_nested_field_info(schema)
        assert "inner_config" in result

    def test_schema_with_array(self):
        schema = {
            "anyOf": [
                {"configuration_types": ["llm"]},
                {"type": "null"}
            ]
        }
        result = extract_nested_field_info(schema)
        # The function stores with the parent key "anyOf"
        assert "anyOf" in result
        assert result["anyOf"]["type"] == "types"
        assert result["anyOf"]["values"] == ["llm"]


class TestProcessSecretFields:
    """Tests for _process_secret_fields logic copied here since the function has module imports."""

    class ConfigurationError(Exception):
        def __init__(self, field, message):
            self.field = field
            self.message = message
            super().__init__(message)

    @staticmethod
    def _process_secret_fields(data: dict, data_properties: dict, config_type: str):
        """Pure function copied from utils.py."""
        from pydantic import SecretStr
        for key, value in list(data.items()):
            if key in data_properties:
                key_properties = data_properties[key]
                is_password = False
                if key_properties.get('format') == 'password':
                    is_password = True
                elif 'anyOf' in key_properties:
                    for schema_option in key_properties['anyOf']:
                        if schema_option.get('format') == 'password':
                            is_password = True
                            break
                if is_password and value and not (isinstance(value, str) and value.startswith('{{secret.') and value.endswith('}}')):
                    data[key] = SecretStr(value)
            else:
                raise TestProcessSecretFields.ConfigurationError(key, f"Property '{key}' is not valid for configuration type '{config_type}'")

    def test_converts_password_field_to_secret(self):
        from pydantic import SecretStr

        data = {"api_key": "my-secret-key"}
        data_properties = {"api_key": {"format": "password"}}
        self._process_secret_fields(data, data_properties, "test_type")

        assert isinstance(data["api_key"], SecretStr)
        assert data["api_key"].get_secret_value() == "my-secret-key"

    def test_skips_already_secret_reference(self):
        data = {"api_key": "{{secret.my_key}}"}
        data_properties = {"api_key": {"format": "password"}}
        self._process_secret_fields(data, data_properties, "test_type")

        assert data["api_key"] == "{{secret.my_key}}"
        assert isinstance(data["api_key"], str)

    def test_handles_anyof_password_format(self):
        from pydantic import SecretStr

        data = {"token": "secret-token"}
        data_properties = {
            "token": {
                "anyOf": [
                    {"type": "string", "format": "password"},
                    {"type": "null"}
                ]
            }
        }
        self._process_secret_fields(data, data_properties, "test_type")

        assert isinstance(data["token"], SecretStr)

    def test_raises_error_for_unknown_field(self):
        data = {"unknown_field": "value"}
        data_properties = {"known_field": {"type": "string"}}

        with pytest.raises(self.ConfigurationError) as exc_info:
            self._process_secret_fields(data, data_properties, "test_type")

        assert "unknown_field" in str(exc_info.value)

    def test_non_password_field_unchanged(self):
        data = {"name": "my-name", "count": 42}
        data_properties = {
            "name": {"type": "string"},
            "count": {"type": "integer"}
        }
        self._process_secret_fields(data, data_properties, "test_type")

        assert data["name"] == "my-name"
        assert data["count"] == 42
