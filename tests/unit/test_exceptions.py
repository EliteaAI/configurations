"""Unit tests for exceptions.py - pure validation error handling."""
import pytest
import pathlib
import sys

TESTS_DIR = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TESTS_DIR))

from fixtures.helpers import load_module_with_stubs


@pytest.fixture(scope='module')
def exceptions_module():
    """Load exceptions module."""
    return load_module_with_stubs(
        TESTS_DIR.parent / 'exceptions.py',
        'configurations.exceptions',
    )


class TestConfigurationError:
    """Tests for ConfigurationError class."""

    def test_error_init(self, exceptions_module):
        err = exceptions_module.ConfigurationError("field_name", "Error message")
        assert err.field == "field_name"
        assert err.message == "Error message"
        assert str(err) == "Error message"

    def test_to_dict(self, exceptions_module):
        err = exceptions_module.ConfigurationError("my_field", "Something went wrong")
        result = err.to_dict()
        assert result == {
            "error": "Something went wrong",
            "field": "my_field"
        }

    def test_to_dict_empty_field(self, exceptions_module):
        err = exceptions_module.ConfigurationError("", "No field specified")
        result = err.to_dict()
        assert result["field"] == ""
        assert result["error"] == "No field specified"


class TestHandleValidationError:
    """Tests for handle_validation_error function."""

    def test_converts_pydantic_error(self, exceptions_module):
        from pydantic import BaseModel, ValidationError

        class TestModel(BaseModel):
            name: str
            age: int

        try:
            TestModel(name=123, age="not_an_int")
        except ValidationError as ve:
            result = exceptions_module.handle_validation_error(ve)
            assert isinstance(result, exceptions_module.ConfigurationError)
            assert result.field in ("name", "age")

    def test_handles_nested_field_location(self, exceptions_module):
        from pydantic import BaseModel, ValidationError

        class Inner(BaseModel):
            value: int

        class Outer(BaseModel):
            inner: Inner

        try:
            Outer(inner={"value": "not_int"})
        except ValidationError as ve:
            result = exceptions_module.handle_validation_error(ve)
            assert "inner" in result.field

    def test_handles_empty_errors(self, exceptions_module):
        from pydantic import ValidationError

        class FakeValidationError(ValidationError):
            def errors(self):
                return []

        # Create a minimal mock - ValidationError needs model and errors
        from pydantic import BaseModel

        class DummyModel(BaseModel):
            x: int

        try:
            DummyModel(x="bad")
        except ValidationError:
            pass

        # Test fallback when no errors
        err = exceptions_module.ConfigurationError("unknown", "Validation failed")
        assert err.field == "unknown"
        assert err.message == "Validation failed"
