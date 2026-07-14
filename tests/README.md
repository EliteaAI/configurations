# Configurations Plugin Tests

Unit tests for the configurations plugin, isolated from Pylon runtime.

## Quick Start

```bash
cd centry/pylon_main/plugins/configurations
python3 tests/run_tests.py -v
```

## Test Structure

```
tests/
├── run_tests.py          # Entry point - installs Pylon stubs before pytest
├── pytest.ini            # Pytest configuration
├── conftest.py           # Auto-markers based on directory
├── requirements-dev.txt  # Test dependencies
├── fixtures/
│   └── helpers.py        # Module loading utilities
└── unit/
    ├── test_exceptions.py     # ConfigurationError, handle_validation_error
    ├── test_utils_pure.py     # extract_nested_field_info, _process_secret_fields
    └── test_utils_models.py   # Model service pure functions
```

## Running Tests

```bash
# All tests
python3 tests/run_tests.py -v

# Unit tests only
python3 tests/run_tests.py -m unit -v

# Specific file
python3 tests/run_tests.py unit/test_exceptions.py -v
```

## Adding Unit Tests

Unit tests go in `tests/unit/`. Focus on pure functions that:
- Take data as parameters (not fetched from DB)
- Return computed results
- Don't require Pylon imports

For functions with imports, copy the pure logic into the test file to avoid module loading issues (see `TestProcessSecretFields` as example).

## Test Count

- **Unit tests**: 30 (exceptions, utils, model service helpers)
