"""Root conftest - auto-marks tests based on directory."""
import pathlib
import pytest

PLUGIN_ROOT = pathlib.Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session")
def plugin_root() -> pathlib.Path:
    """Absolute path to the configurations plugin root."""
    return PLUGIN_ROOT


@pytest.fixture(scope="session")
def models_path(plugin_root: pathlib.Path) -> pathlib.Path:
    """Path to the models/ directory."""
    return plugin_root / "models"


@pytest.fixture(scope="session")
def utils_path(plugin_root: pathlib.Path) -> pathlib.Path:
    """Path to the utils/ directory."""
    return plugin_root / "utils"


def pytest_collection_modifyitems(items):
    """Auto-mark tests based on their directory location."""
    for item in items:
        test_path = pathlib.Path(item.fspath)
        parts = test_path.parts

        if "unit" in parts:
            item.add_marker("unit")
        elif "integration" in parts:
            item.add_marker("integration")
