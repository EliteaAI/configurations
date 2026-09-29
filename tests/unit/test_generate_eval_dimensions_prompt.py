"""The generate_eval_dimensions default prompt must ask for preset scales, weights and targets,
and past defaults stay frozen."""

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
    "GENERATE_EVAL_DIMENSIONS_DEFAULT_PROMPT_V3",
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
    assert defaults.GENERATE_EVAL_DIMENSIONS_DEFAULT_PROMPT_V3.strip() != current


def test_past_defaults_told_the_model_to_leave_targets_null(defaults):
    # Guards the V2 snapshot against being "fixed" in place — it must stay the text that was
    # seeded, or the migration task stops recognising those rows.
    assert "null" in defaults.GENERATE_EVAL_DIMENSIONS_DEFAULT_PROMPT_V2
    assert "{custom_instructions_clause}" in defaults.GENERATE_EVAL_DIMENSIONS_DEFAULT_PROMPT_V2
    assert "{custom_instructions_clause}" not in defaults.GENERATE_EVAL_DIMENSIONS_DEFAULT_PROMPT_V1


def test_v3_asked_for_a_non_preset_scale_and_free_float_weights(defaults):
    # Guards the V3 snapshot against being "fixed" in place — it must stay the text that was
    # seeded, or the migration task stops recognising those rows.
    prompt = defaults.GENERATE_EVAL_DIMENSIONS_DEFAULT_PROMPT_V3

    assert '"scale_min": 0,' in prompt
    assert "<float >= 0, relative importance" in prompt
    assert "always propose the threshold" in prompt


def test_current_default_asks_for_a_target_on_the_native_scale(defaults):
    prompt = defaults.GENERATE_EVAL_DIMENSIONS_DEFAULT_PROMPT

    assert "never a percentage or 0-1 value" in prompt
    assert '"target": <same as default_target>' in prompt
    assert '"target_operator": <same as default_target_operator>' in prompt


def test_current_default_only_offers_operators_the_ui_supports(defaults):
    prompt = defaults.GENERATE_EVAL_DIMENSIONS_DEFAULT_PROMPT

    assert "operator in {{>=, <=, ==}}" in prompt
    assert '">"' not in prompt
    assert '"<"' not in prompt


def test_current_default_only_offers_the_ui_scale_presets(defaults):
    # Anything else opens as a Custom scale in the dimension form (EliteaUI SCALE_TYPE_PRESET_CONFIG).
    prompt = defaults.GENERATE_EVAL_DIMENSIONS_DEFAULT_PROMPT

    assert '"continuous", scale_min 1, scale_max 100' in prompt
    assert '"ordinal", scale_min 1, scale_max 5' in prompt
    assert '"binary", scale_min 0, scale_max 1' in prompt
    assert "never Custom" in prompt


def test_current_default_only_offers_the_ui_importance_weights(defaults):
    # Must match EliteaUI IMPORTANCE_WEIGHT_MAP, or the draft opens with Custom importance.
    prompt = defaults.GENERATE_EVAL_DIMENSIONS_DEFAULT_PROMPT

    for level, weight in (("Low", 1), ("Medium", 2), ("High", 3), ("Critical", 4)):
        assert f"- {level} = {weight}:" in prompt
    assert '"default_weight": <1|2|3|4>' in prompt


def test_current_default_covers_pass_fail_targets(defaults):
    prompt = defaults.GENERATE_EVAL_DIMENSIONS_DEFAULT_PROMPT

    assert "Must pass: target 1, target_operator \"==\"" in prompt
    assert "polarity is always \"higher_better\"" in prompt
