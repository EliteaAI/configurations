"""EL-7325: service prompts for drafting one pipeline node with AI (AC-P1, AC-P2).

Run via:
    python tests/run_tests.py unit/test_7325_node_draft_prompts.py -v
"""
import importlib.util
import json
import pathlib
import re
import sys
import types

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
PKG = "cfg_7325_node_draft_prompts_test"

NODE_TYPES = (
    "llm", "agent", "toolkit", "mcp", "code",
    "router", "decision", "hitl", "state_modifier", "printer",
)
CORE_KEYS = ("pipeline_node_build", "pipeline_node_edit")
SPEC_KEYS = tuple(f"pipeline_node_spec_{t}" for t in NODE_TYPES)
GENERIC_SPEC_KEY = "pipeline_node_spec_generic"
NODE_KEYS = CORE_KEYS + SPEC_KEYS + (GENERIC_SPEC_KEY,)
MAX_CHARS = 2500
EXAMPLE_MARKER = "Example output:"
OUTPUT_KEYS = {"summary", "node", "new_state", "warnings"}
STATE_TYPES = {"str", "list", "dict", "int", "float", "bool"}


@pytest.fixture(scope="module")
def mods():
    pkg = types.ModuleType(PKG)
    pkg.__path__ = [str(ROOT / "models/pd")]
    sys.modules[PKG] = pkg
    try:
        for name in ("service_prompt_keys", "service_prompt_defaults"):
            full = f"{PKG}.{name}"
            spec = importlib.util.spec_from_file_location(full, ROOT / f"models/pd/{name}.py")
            mod = importlib.util.module_from_spec(spec)
            sys.modules[full] = mod
            spec.loader.exec_module(mod)
        yield types.SimpleNamespace(
            keys=sys.modules[f"{PKG}.service_prompt_keys"].SERVICE_PROMPT_KEYS,
            defaults=sys.modules[f"{PKG}.service_prompt_defaults"].SERVICE_PROMPT_DEFAULTS,
        )
    finally:
        for name in list(sys.modules):
            if name.startswith(PKG):
                del sys.modules[name]


def _example_objects(text):
    """Every JSON object that follows an 'Example output:' marker or an '->' arrow."""
    decoder = json.JSONDecoder()
    found = []
    for match in re.finditer(rf"{re.escape(EXAMPLE_MARKER)}|->", text):
        start = text.find("{", match.end())
        assert start != -1, f"No JSON after marker at {match.start()}"
        obj, _ = decoder.raw_decode(text[start:])
        found.append(obj)
    return found


@pytest.mark.parametrize("key", NODE_KEYS)
def test_key_registered_with_default(mods, key):
    assert key in mods.keys
    assert mods.defaults[key].strip()


def test_existing_field_assistant_keys_kept(mods):
    for key in ("code_assistant", "router_assistant", "state_modifier_assistant", "llm_system_assistant",
                "llm_task_assistant", "decision_assistant", "printer_assistant", "configure_node_with_ai"):
        assert key in mods.keys


@pytest.mark.parametrize("key", NODE_KEYS)
def test_size_limit(mods, key):
    assert len(mods.defaults[key]) <= MAX_CHARS


@pytest.mark.parametrize("key", NODE_KEYS)
def test_no_markdown_fences(mods, key):
    assert "```" not in mods.defaults[key]


@pytest.mark.parametrize("key", SPEC_KEYS)
def test_spec_names_its_type_and_has_one_example(mods, key):
    text = mods.defaults[key]
    node_type = key.removeprefix("pipeline_node_spec_")
    assert text.startswith(f"Node type: {node_type}.")
    assert text.count(EXAMPLE_MARKER) == 1


def test_edit_core_has_minimal_change_example(mods):
    assert len(_example_objects(mods.defaults["pipeline_node_edit"])) == 1


@pytest.mark.parametrize("key", SPEC_KEYS + ("pipeline_node_edit",))
def test_examples_follow_output_contract(mods, key):
    for obj in _example_objects(mods.defaults[key]):
        assert set(obj) == OUTPUT_KEYS
        assert isinstance(obj["summary"], str) and len(obj["summary"]) <= 200
        assert isinstance(obj["node"], dict)
        assert "id" not in obj["node"] and "type" not in obj["node"]
        assert isinstance(obj["warnings"], list)
        for var in obj["new_state"]:
            assert set(var) == {"name", "type"}
            assert var["type"] in STATE_TYPES


@pytest.mark.parametrize("key", NODE_KEYS)
def test_no_format_placeholders_needed(mods, key):
    """Prompts are concatenated, never .format()-ed: no {{name}} slots are allowed (AC-39)."""
    assert not re.search(r"\{\{[a-z_]+\}\}", mods.defaults[key])


def test_generic_spec_has_no_example_and_no_type(mods):
    """The fallback for a node type with no spec: type-agnostic, the server appends the registry facts."""
    text = mods.defaults[GENERIC_SPEC_KEY]
    assert EXAMPLE_MARKER not in text
    assert not text.startswith("Node type:")
