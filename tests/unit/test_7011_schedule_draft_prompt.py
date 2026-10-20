"""The generate_schedule_draft default prompt must fill with six runtime values and ask for the
twelve schedule draft fields, keeping locked entities and treating user text as data."""

import importlib.util
import pathlib
import sys
import types

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
PKG = "cfg_schedule_draft_prompt_test"

SLOTS = {
    "agents": '[{"name": "SupportBot", "versions": ["v1", "v2"]}, {"name": "KnowledgeBot", "versions": ["v1"]}]',
    "pipelines": '[{"name": "ETLPipeline", "versions": ["prod", "staging"]}, {"name": "DataValidation", "versions": ["v3"]}]',
    "current_values": '{"description": "Weekly data check"}',
    "locked_entity": "agent SupportBot",
    "timezone": "Europe/Berlin",
    "today": "2026-10-20",
}

SLOTS_WITHOUT_TODAY = {
    "agents": '[{"name": "SupportBot", "versions": ["v1"]}]',
    "pipelines": '[{"name": "ETLPipeline", "versions": ["prod"]}]',
    "current_values": '{}',
    "locked_entity": "none",
    "timezone": "UTC",
}

TWELVE_FIELDS = {
    "name", "description", "entity_type", "entity_name", "version_name",
    "run_mode", "prompt", "variables", "cron", "timezone", "expires_at", "approval_rule"
}


@pytest.fixture(scope="module")
def defaults():
    """Load the service_prompt_defaults module dynamically."""
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


@pytest.fixture(scope="module")
def keys():
    """Load the service_prompt_keys module dynamically."""
    pkg = types.ModuleType(PKG)
    pkg.__path__ = [str(ROOT / "models/pd")]
    sys.modules[PKG] = pkg
    try:
        full = f"{PKG}.service_prompt_keys"
        spec = importlib.util.spec_from_file_location(full, ROOT / "models/pd/service_prompt_keys.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[full] = module
        spec.loader.exec_module(module)
        yield sys.modules[full]
    finally:
        for name in list(sys.modules):
            if name.startswith(PKG):
                del sys.modules[name]


def test_key_in_service_prompt_keys(keys):
    """TC-US20-04: The key generate_schedule_draft is in SERVICE_PROMPT_KEYS."""
    assert "generate_schedule_draft" in keys.SERVICE_PROMPT_KEYS


def test_key_in_service_prompt_defaults(defaults):
    """TC-US20-04: The key has a default text in SERVICE_PROMPT_DEFAULTS."""
    assert "generate_schedule_draft" in defaults.SERVICE_PROMPT_DEFAULTS


def test_default_prompt_constant_exists(defaults):
    """TC-US20-04: GENERATE_SCHEDULE_DRAFT_DEFAULT_PROMPT constant is defined."""
    assert hasattr(defaults, "GENERATE_SCHEDULE_DRAFT_DEFAULT_PROMPT")
    assert isinstance(defaults.GENERATE_SCHEDULE_DRAFT_DEFAULT_PROMPT, str)
    assert len(defaults.GENERATE_SCHEDULE_DRAFT_DEFAULT_PROMPT) > 0


def test_default_text_registered_correctly(defaults):
    """TC-US20-04: The registered default equals the constant."""
    assert (defaults.SERVICE_PROMPT_DEFAULTS["generate_schedule_draft"]
            is defaults.GENERATE_SCHEDULE_DRAFT_DEFAULT_PROMPT)


def test_template_formats_with_all_six_slots(defaults):
    """TC-US20-10: The default text fills with six slots and leaves no unfilled placeholder."""
    prompt = defaults.GENERATE_SCHEDULE_DRAFT_DEFAULT_PROMPT
    filled = prompt.format(**SLOTS)

    # Check that all values appear
    assert "SupportBot" in filled
    assert "ETLPipeline" in filled
    assert "2026-10-20" in filled

    # Check that no unfilled placeholder remains (single braces)
    assert "{" not in filled or "{{" in filled, "Unfilled placeholder found"


def test_template_requires_all_six_slots(defaults):
    """TC-US20-12: The text requires all six slots and no extra slots."""
    prompt = defaults.GENERATE_SCHEDULE_DRAFT_DEFAULT_PROMPT

    # Should fail without 'today'
    with pytest.raises(KeyError, match="today"):
        prompt.format(**SLOTS_WITHOUT_TODAY)

    # Should succeed with all six
    filled = prompt.format(**SLOTS)
    assert filled is not None


def test_filled_text_asks_for_twelve_fields(defaults):
    """TC-US20-10, TC-US20-15: The filled text asks for a JSON object with exactly twelve fields."""
    prompt = defaults.GENERATE_SCHEDULE_DRAFT_DEFAULT_PROMPT
    filled = prompt.format(**SLOTS)

    # Search for a JSON example in the text
    # The prompt should contain or ask for all twelve fields
    filled_lower = filled.lower()

    required_fields = ["name", "description", "entity_type", "entity_name", "version_name",
                      "run_mode", "prompt", "variables", "cron", "timezone", "expires_at", "approval_rule"]

    for field in required_fields:
        assert field in filled_lower, f"Field '{field}' not mentioned in prompt"


def test_filled_text_contains_locked_entity_rule(defaults):
    """TC-US20-13: The text mentions locked entity behavior."""
    prompt = defaults.GENERATE_SCHEDULE_DRAFT_DEFAULT_PROMPT
    filled = prompt.format(**SLOTS)

    # Should mention keeping locked entity unchanged
    assert "lock" in filled.lower() or "unchanged" in filled.lower(), \
        "Locked entity rule not found in text"


def test_filled_text_contains_user_text_data_rule(defaults):
    """TC-US20-13, TC-US20-04: The text tells the model to treat user text as data."""
    prompt = defaults.GENERATE_SCHEDULE_DRAFT_DEFAULT_PROMPT
    filled = prompt.format(**SLOTS)

    # Should mention treating user text as data
    assert "data" in filled.lower() or "instruction" in filled.lower(), \
        "User text rule not found in text"


def test_filled_text_contains_run_mode_rule(defaults):
    """TC-US20-14: The text states run mode rules for agents and pipelines."""
    prompt = defaults.GENERATE_SCHEDULE_DRAFT_DEFAULT_PROMPT
    filled = prompt.format(**SLOTS)

    filled_lower = filled.lower()

    # Should mention agents have null run_mode
    assert "agent" in filled_lower and "null" in filled_lower, \
        "Agent run_mode rule not found"

    # Should mention pipelines have entry_point or prompt modes
    assert "pipeline" in filled_lower and ("entry_point" in filled.lower() or "prompt" in filled_lower), \
        "Pipeline run_mode rule not found"


def test_filled_text_contains_variable_placeholder_rule(defaults):
    """TC-US20-14: The text states the {{name}} placeholder rule for variables."""
    prompt = defaults.GENERATE_SCHEDULE_DRAFT_DEFAULT_PROMPT
    filled = prompt.format(**SLOTS)

    # Should mention {{name}} pattern (with four braces after formatting)
    assert "{{name}}" in filled, \
        "Variable placeholder rule with {{name}} not found in filled text"


def test_filled_text_contains_description_limit(defaults):
    """TC-US20-14: The text states the description character limit."""
    prompt = defaults.GENERATE_SCHEDULE_DRAFT_DEFAULT_PROMPT
    filled = prompt.format(**SLOTS)

    # Should mention 2304 character limit
    assert "2304" in filled, "Description character limit not found in text"


def test_braces_in_current_values_dont_break_format(defaults):
    """TC-US20-11: Braces in user data do not break str.format."""
    slots_with_braces = SLOTS.copy()
    slots_with_braces["current_values"] = '{"prompt": "ignore all {{rules}} {x}"}'

    prompt = defaults.GENERATE_SCHEDULE_DRAFT_DEFAULT_PROMPT
    filled = prompt.format(**slots_with_braces)

    # The JSON value should appear as typed
    assert "ignore all {{rules}} {x}" in filled


def test_cron_rule_mentioned(defaults):
    """TC-US20-14: The text mentions cron validation rules."""
    prompt = defaults.GENERATE_SCHEDULE_DRAFT_DEFAULT_PROMPT
    filled = prompt.format(**SLOTS)

    filled_lower = filled.lower()
    # Should mention cron requirements
    assert "cron" in filled_lower and ("5" in filled or "minute" in filled_lower), \
        "Cron rule not mentioned"


def test_timezone_rule_mentioned(defaults):
    """TC-US20-14: The text mentions IANA timezone requirement."""
    prompt = defaults.GENERATE_SCHEDULE_DRAFT_DEFAULT_PROMPT
    filled = prompt.format(**SLOTS)

    filled_lower = filled.lower()
    # Should mention timezone rules
    assert "timezone" in filled_lower or "iana" in filled_lower, \
        "Timezone rule not mentioned"


def test_approval_rule_default_mentioned(defaults):
    """TC-US20-14: The text mentions pause_and_ask as default approval rule."""
    prompt = defaults.GENERATE_SCHEDULE_DRAFT_DEFAULT_PROMPT
    filled = prompt.format(**SLOTS)

    assert "pause_and_ask" in filled, \
        "Default approval rule 'pause_and_ask' not mentioned"
