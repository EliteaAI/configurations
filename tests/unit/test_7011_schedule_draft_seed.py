"""The real seed creates the generate_schedule_draft row once and never overwrites it (R1.1, R1.2).

Only the collaborators are stubbed: the Public project lookup, the row store and the pylon log.
The seed module and the prompt defaults are the real files.
"""

import importlib.util
import pathlib
import sys
import types

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
PKG = "cfg_7011_seed_test"
KEY = "generate_schedule_draft"


class FakeLog:
    def __init__(self):
        self.warnings = []

    def warning(self, message, *args, **kwargs):
        self.warnings.append(message)


class FakeStore:
    """In-memory rows with the create_if_not_exists contract: (row, created)."""

    def __init__(self):
        self.rows = {}

    def create_if_not_exists(self, payload):
        key = (payload["project_id"], payload["elitea_title"])
        if key in self.rows:
            return self.rows[key], False
        self.rows[key] = payload
        return payload, True


def _load(name, path):
    spec = importlib.util.spec_from_file_location(f"{PKG}.{name}", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[f"{PKG}.{name}"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def seed(monkeypatch):
    """Load the real service_prompt_seed.py; return (module, store, log, project_id holder)."""
    store, log, state = FakeStore(), FakeLog(), {"project_id": 42}

    def get_public_project_id():
        return state["project_id"]

    pkg = types.ModuleType(PKG)
    pkg.__path__ = [str(ROOT)]
    models = types.ModuleType(f"{PKG}.models")
    models.__path__ = [str(ROOT / "models")]
    pd = types.ModuleType(f"{PKG}.models.pd")
    pd.__path__ = [str(ROOT / "models/pd")]
    common_utils = types.ModuleType(f"{PKG}.common_utils")
    common_utils.get_public_project_id = get_public_project_id
    utils = types.ModuleType(f"{PKG}.utils")
    utils.create_if_not_exists = store.create_if_not_exists
    pylon_tools = types.ModuleType("pylon.core.tools")
    pylon_tools.log = log

    stubs = {
        "pylon": types.ModuleType("pylon"),
        "pylon.core": types.ModuleType("pylon.core"),
        "pylon.core.tools": pylon_tools,
        PKG: pkg,
        f"{PKG}.models": models,
        f"{PKG}.models.pd": pd,
        f"{PKG}.common_utils": common_utils,
        f"{PKG}.utils": utils,
    }
    for name, module in stubs.items():
        monkeypatch.setitem(sys.modules, name, module)
    try:
        _load("models.pd.service_prompt_keys", ROOT / "models/pd/service_prompt_keys.py")
        defaults = _load("models.pd.service_prompt_defaults", ROOT / "models/pd/service_prompt_defaults.py")
        module = _load("service_prompt_seed", ROOT / "service_prompt_seed.py")
        yield types.SimpleNamespace(
            run=module.ensure_default_service_prompts,
            defaults=defaults.SERVICE_PROMPT_DEFAULTS,
            store=store,
            log=log,
            state=state,
        )
    finally:
        for name in list(sys.modules):
            if name.startswith(f"{PKG}."):
                del sys.modules[name]


def test_seed_creates_missing_row(seed):
    """TC-US20-01 (R1.1): the missing row is created with the default text."""
    seed.run()

    row = seed.store.rows[(42, KEY)]
    assert row["data"] == {"key": KEY, "prompt": seed.defaults[KEY]}
    assert row["type"] == "service_prompt"
    assert row["label"] == "Generate Schedule Draft"


def test_seed_keeps_existing_row(seed):
    """TC-US20-02 (R1.2): two runs leave an edited row unchanged and add no second row."""
    seed.store.rows[(42, KEY)] = {
        "project_id": 42,
        "elitea_title": KEY,
        "data": {"key": KEY, "prompt": "Custom text"},
    }

    seed.run()
    seed.run()

    assert seed.store.rows[(42, KEY)]["data"]["prompt"] == "Custom text"
    assert [k for k in seed.store.rows if k[1] == KEY] == [(42, KEY)]


def test_seed_skips_without_public_project(seed):
    """TC-US20-03 (R1.1): no Public project id means a warning, no rows and no error."""
    seed.state["project_id"] = None

    seed.run()

    assert seed.store.rows == {}
    assert seed.log.warnings
