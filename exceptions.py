"""
Simple exceptions for configuration validation errors.
"""

from typing import Dict, Any
from pydantic import ValidationError


class ConfigurationError(Exception):
    """Simple configuration error with field and message."""

    def __init__(self, field: str, message: str):
        self.field = field
        self.message = message
        super().__init__(self.message)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to API response format."""
        return {
            "error": self.message,
            "field": self.field
        }


def handle_validation_error(validation_error: ValidationError) -> ConfigurationError:
    """Convert Pydantic ValidationError to simple ConfigurationError."""
    if validation_error.errors():
        error = validation_error.errors()[0]  # Just take the first error
        field = ".".join(str(loc) for loc in error["loc"])
        message = error["msg"]
        return ConfigurationError(field, message)

    return ConfigurationError("unknown", "Validation failed")
