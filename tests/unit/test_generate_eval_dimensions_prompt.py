"""The generate_eval_dimensions default prompt must ask for targets, and past defaults stay frozen."""

import importlib.util
import pathlib
import sys
import types

import pytest


ROOT = pathlib.Path(__file__).resolve().parents[2]
PKG = "cfg_eval_dimensions_prompt_test"

SLOTS = {
    "application_name": "Support Bot",
    "instructions": "Answer support tickets politely.",
    "count_clause": "Propose 3-6 dimensions, using your judgment.",
    "existing_dimensions": "The project's dimension library is currently empty.",
    "custom_instructions_clause": "",
}


@pytest.fixture(scope="module")
def defaults():
    pkg = types.ModuleType(PKG)
    pkg.__path__ = [str(ROOT / "models/pd")]
    sys.modules[PKG] = pkg
    try:
        for name in ("service_prompt_keys", "service_prompt_defaults"):
            full = f"{PKG}.{name}"
            spec = importlib.util.spec_from_file_location(full, ROOT / f"models/pd/{name}.py")
            module = importlib.util.module_from_spec(spec)
            sys.modules[full] = module
            spec.loader.exec_module(module)
        yield sys.modules[f"{PKG}.service_prompt_defaults"]
    finally:
        for name in list(sys.modules):
            if name.startswith(PKG):
                del sys.modules[name]


@pytest.mark.parametrize("attr", [
    "GENERATE_EVAL_DIMENSIONS_DEFAULT_PROMPT",
    "GENERATE_EVAL_DIMENSIONS_DEFAULT_PROMPT_V2",
])
def test_template_formats_with_all_builder_slots(defaults, attr):
    out = getattr(defaults, attr).format(**SLOTS)

    assert "Support Bot" in out
    assert "{{" not in out


def test_v1_formats_without_custom_instructions_slot(defaults):
    slots = {k: v for k, v in SLOTS.items() if k != "custom_instructions_clause"}

    assert "Support Bot" in defaults.GENERATE_EVAL_DIMENSIONS_DEFAULT_PROMPT_V1.format(**slots)


def test_current_default_is_registered(defaults):
    assert (defaults.SERVICE_PROMPT_DEFAULTS["generate_eval_dimensions"]
            is defaults.GENERATE_EVAL_DIMENSIONS_DEFAULT_PROMPT)


def test_past_defaults_differ_from_current(defaults):
    current = defaults.GENERATE_EVAL_DIMENSIONS_DEFAULT_PROMPT.strip()

    assert defaults.GENERATE_EVAL_DIMENSIONS_DEFAULT_PROMPT_V1.strip() != current
    assert defaults.GENERATE_EVAL_DIMENSIONS_DEFAULT_PROMPT_V2.strip() != current


def test_past_defaults_told_the_model_to_leave_targets_null(defaults):
    # Guards the V2 snapshot against being "fixed" in place — it must stay the text that was
    # seeded, or the migration task stops recognising those rows.
    assert "null" in defaults.GENERATE_EVAL_DIMENSIONS_DEFAULT_PROMPT_V2
    assert "{custom_instructions_clause}" in defaults.GENERATE_EVAL_DIMENSIONS_DEFAULT_PROMPT_V2
    assert "{custom_instructions_clause}" not in defaults.GENERATE_EVAL_DIMENSIONS_DEFAULT_PROMPT_V1


def test_current_default_asks_for_a_target_on_the_native_scale(defaults):
    prompt = defaults.GENERATE_EVAL_DIMENSIONS_DEFAULT_PROMPT

    assert "always propose the threshold" in prompt
    assert "not a percentage" in prompt
    assert '"target": <same value as default_target>' in prompt
    assert '"target_operator": <same value as default_target_operator>' in prompt


def test_current_default_only_offers_operators_the_ui_supports(defaults):
    prompt = defaults.GENERATE_EVAL_DIMENSIONS_DEFAULT_PROMPT

    assert '">=", "<=" or "=="' in prompt
    assert '">"' not in prompt
    assert '"<"' not in prompt


def test_current_default_covers_each_scale_kind(defaults):
    prompt = defaults.GENERATE_EVAL_DIMENSIONS_DEFAULT_PROMPT

    assert "continuous, higher_better" in prompt
    assert "continuous, lower_better" in prompt
    assert "ordinal, higher_better" in prompt
    assert "ordinal, lower_better" in prompt
    assert 'binary: "==" 1' in prompt
    assert 'A "binary" dimension is always "higher_better"' in prompt
