"""Unit tests for EL-7323 pipeline service prompt keys, defaults, and template slots.

Covers:
- Four new keys registered in SERVICE_PROMPT_KEYS
- SERVICE_PROMPT_DEFAULTS maps each key to a non-empty string
- Template slots for generate_pipeline_draft, edit_pipeline_draft, configure_node_with_ai
- pipeline_authoring_reference is a non-template reference document (not format()-able)
- PIPELINE_PROMPT_PAST_DEFAULTS contains migration entries for the versioned prompts, schedule draft included
- ServicePrompt model accepts / rejects the new keys correctly

Run via:
    python tests/run_tests.py unit/test_7323_pipeline_service_prompts.py -v
"""
import importlib.util
import pathlib
import string
import sys
import types

import pytest
from pydantic import ValidationError

ROOT = pathlib.Path(__file__).resolve().parents[2]
PKG = "cfg_7323_pipeline_prompts_test"

NEW_PIPELINE_KEYS = (
    "configure_node_with_ai",
    "edit_pipeline_draft",
    "generate_pipeline_draft",
    "pipeline_authoring_reference",
)

GENERATE_SLOTS = {"toolkits", "mcp", "agents", "pipelines", "skills"}
EDIT_SLOTS = {"current_config", "toolkits", "mcp", "agents", "pipelines", "skills"}
CONFIGURE_NODE_SLOTS = {"node_type", "state_variables", "available_nodes"}

GENERATE_SAMPLE = {
    "toolkits": "- jira_toolkit",
    "mcp": "none",
    "agents": "none",
    "pipelines": "none",
    "skills": "none",
}
EDIT_SAMPLE = {**GENERATE_SAMPLE, "current_config": "name: old-pipe\npipeline_yaml: ..."}
CONFIGURE_SAMPLE = {
    "node_type": "llm",
    "state_variables": "input_text, summary",
    "available_nodes": "step2, step3",
}


@pytest.fixture(scope="module")
def mods():
    """Load service_prompt_keys, service_prompt_defaults and service_prompt under a stub package."""
    pkg = types.ModuleType(PKG)
    pkg.__path__ = [str(ROOT / "models/pd")]
    sys.modules[PKG] = pkg
    try:
        for name in ("service_prompt_keys", "service_prompt_defaults", "service_prompt"):
            full = f"{PKG}.{name}"
            spec = importlib.util.spec_from_file_location(full, ROOT / f"models/pd/{name}.py")
            mod = importlib.util.module_from_spec(spec)
            sys.modules[full] = mod
            spec.loader.exec_module(mod)
        yield types.SimpleNamespace(
            keys=sys.modules[f"{PKG}.service_prompt_keys"],
            defaults=sys.modules[f"{PKG}.service_prompt_defaults"],
            model=sys.modules[f"{PKG}.service_prompt"].ServicePrompt,
        )
    finally:
        for name in list(sys.modules):
            if name.startswith(PKG):
                del sys.modules[name]


# ---------------------------------------------------------------------------
# Keys and defaults registration
# ---------------------------------------------------------------------------

class TestKeysRegistered:
    @pytest.mark.parametrize("key", NEW_PIPELINE_KEYS)
    def test_key_in_service_prompt_keys(self, mods, key):
        assert key in mods.keys.SERVICE_PROMPT_KEYS

    @pytest.mark.parametrize("key", NEW_PIPELINE_KEYS)
    def test_key_in_service_prompt_defaults(self, mods, key):
        assert key in mods.defaults.SERVICE_PROMPT_DEFAULTS

    @pytest.mark.parametrize("key", NEW_PIPELINE_KEYS)
    def test_default_is_non_empty_string(self, mods, key):
        text = mods.defaults.SERVICE_PROMPT_DEFAULTS[key]
        assert isinstance(text, str)
        assert text.strip()

    def test_all_keys_have_defaults(self, mods):
        """Every key in SERVICE_PROMPT_KEYS has a corresponding default."""
        for key in mods.keys.SERVICE_PROMPT_KEYS:
            assert key in mods.defaults.SERVICE_PROMPT_DEFAULTS, f"Missing default for {key!r}"

    def test_all_defaults_have_keys(self, mods):
        """Every key in SERVICE_PROMPT_DEFAULTS is an allowed key."""
        for key in mods.defaults.SERVICE_PROMPT_DEFAULTS:
            assert key in mods.keys.SERVICE_PROMPT_KEYS, f"Orphaned default for {key!r}"


# ---------------------------------------------------------------------------
# ServicePrompt model validation
# ---------------------------------------------------------------------------

class TestServicePromptModel:
    @pytest.mark.parametrize("key", NEW_PIPELINE_KEYS)
    def test_model_accepts_new_keys(self, mods, key):
        obj = mods.model(key=key, prompt="some prompt text")
        assert obj.key == key

    def test_model_rejects_unknown_key(self, mods):
        with pytest.raises(ValidationError, match="Unknown service prompt key"):
            mods.model(key="generate_pipeline_graph", prompt="text")

    @pytest.mark.parametrize("key", NEW_PIPELINE_KEYS)
    def test_empty_prompt_rejected(self, mods, key):
        with pytest.raises(ValidationError, match="at least 1 character"):
            mods.model(key=key, prompt="")


# ---------------------------------------------------------------------------
# Template slots — generate_pipeline_draft
# ---------------------------------------------------------------------------

class TestGeneratePipelineDraftSlots:
    @pytest.fixture(scope="class")
    @classmethod
    def prompt(cls, mods):
        return mods.defaults.SERVICE_PROMPT_DEFAULTS["generate_pipeline_draft"]

    def test_has_exactly_five_slots(self, prompt):
        slots = {f for _, f, _, _ in string.Formatter().parse(prompt) if f}
        assert slots == GENERATE_SLOTS

    def test_fills_without_error(self, prompt):
        filled = prompt.format(**GENERATE_SAMPLE)
        assert filled.strip()

    def test_missing_slot_raises(self, prompt):
        incomplete = {k: v for k, v in GENERATE_SAMPLE.items() if k != "toolkits"}
        with pytest.raises(KeyError, match="toolkits"):
            prompt.format(**incomplete)

    def test_filled_text_contains_slot_values(self, prompt):
        filled = prompt.format(**GENERATE_SAMPLE)
        for value in GENERATE_SAMPLE.values():
            assert value in filled

    def test_transition_end_rule_present(self, prompt):
        assert "transition: END" in prompt

    def test_entry_point_rule_present(self, prompt):
        assert "entry_point" in prompt


# ---------------------------------------------------------------------------
# Template slots — edit_pipeline_draft
# ---------------------------------------------------------------------------

class TestEditPipelineDraftSlots:
    @pytest.fixture(scope="class")
    @classmethod
    def prompt(cls, mods):
        return mods.defaults.SERVICE_PROMPT_DEFAULTS["edit_pipeline_draft"]

    def test_has_exactly_six_slots(self, prompt):
        slots = {f for _, f, _, _ in string.Formatter().parse(prompt) if f}
        assert slots == EDIT_SLOTS

    def test_fills_without_error(self, prompt):
        filled = prompt.format(**EDIT_SAMPLE)
        assert filled.strip()

    def test_current_config_slot_present(self, prompt):
        assert "{current_config}" in prompt

    def test_missing_current_config_raises(self, prompt):
        incomplete = {k: v for k, v in EDIT_SAMPLE.items() if k != "current_config"}
        with pytest.raises(KeyError, match="current_config"):
            prompt.format(**incomplete)

    def test_return_complete_yaml_rule_present(self, prompt):
        assert "COMPLETE" in prompt


# ---------------------------------------------------------------------------
# Template slots — configure_node_with_ai
# ---------------------------------------------------------------------------

class TestConfigureNodeWithAiSlots:
    @pytest.fixture(scope="class")
    @classmethod
    def prompt(cls, mods):
        return mods.defaults.SERVICE_PROMPT_DEFAULTS["configure_node_with_ai"]

    def test_has_exactly_three_slots(self, prompt):
        slots = {f for _, f, _, _ in string.Formatter().parse(prompt) if f}
        assert slots == CONFIGURE_NODE_SLOTS

    def test_fills_without_error(self, prompt):
        filled = prompt.format(**CONFIGURE_SAMPLE)
        assert filled.strip()

    def test_no_id_or_type_rule_present(self, prompt):
        assert "Do NOT include `id` or `type`" in prompt

    def test_filled_text_contains_slot_values(self, prompt):
        filled = prompt.format(**CONFIGURE_SAMPLE)
        for value in CONFIGURE_SAMPLE.values():
            assert value in filled


# ---------------------------------------------------------------------------
# pipeline_authoring_reference — not a format string
# ---------------------------------------------------------------------------

class TestPipelineAuthoringReference:
    @pytest.fixture(scope="class")
    @classmethod
    def prompt(cls, mods):
        return mods.defaults.SERVICE_PROMPT_DEFAULTS["pipeline_authoring_reference"]

    def test_is_a_reference_document(self, prompt):
        assert "Pipeline Authoring Reference" in prompt

    def test_covers_node_types(self, prompt):
        for node_type in ("llm", "code", "agent", "toolkit", "router", "decision"):
            assert node_type in prompt

    def test_documents_transition_end_sentinel(self, prompt):
        assert "transition: END" in prompt

    def test_documents_entry_point(self, prompt):
        assert "entry_point" in prompt


# ---------------------------------------------------------------------------
# PIPELINE_PROMPT_PAST_DEFAULTS — migration registry
# ---------------------------------------------------------------------------

class TestPipelinePromptPastDefaults:
    @pytest.fixture(scope="class")
    @classmethod
    def past(cls, mods):
        return mods.defaults.PIPELINE_PROMPT_PAST_DEFAULTS

    def test_contains_expected_keys(self, past):
        assert "pipeline_authoring_reference" in past
        assert "generate_pipeline_draft" in past
        assert "edit_pipeline_draft" in past
        assert "generate_schedule_draft" in past

    def test_each_entry_is_non_empty_tuple(self, past):
        for key, versions in past.items():
            assert isinstance(versions, tuple), f"{key}: expected tuple"
            assert len(versions) >= 1, f"{key}: tuple must not be empty"
            for v in versions:
                assert isinstance(v, str) and v.strip(), f"{key}: each version must be non-empty"

    def test_current_defaults_not_in_past_defaults(self, mods, past):
        """Current default must differ from every past version (they represent old/stale content)."""
        for key, versions in past.items():
            current = mods.defaults.SERVICE_PROMPT_DEFAULTS[key].strip()
            for v in versions:
                assert current != v.strip(), (
                    f"{key}: current default must not equal a past version "
                    f"(would cause migration to report it as 'up_to_date' for wrong reasons)"
                )

    def test_configure_node_not_in_past_defaults(self, past):
        """configure_node_with_ai has no past versions — it is new in EL-7323."""
        assert "configure_node_with_ai" not in past
