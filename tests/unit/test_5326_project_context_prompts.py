"""Default Project Context generator prompts must emit the activation classifier."""

import pathlib


ROOT = pathlib.Path(__file__).resolve().parents[2]


def test_create_and_edit_defaults_require_activation_description():
    source = (ROOT / "models/pd/service_prompt_defaults.py").read_text()

    create_prompt = source[
        source.index("PROJECT_CONTEXT_GENERATOR_DEFAULT_PROMPT"):
        source.index("EDIT_PROJECT_CONTEXT_DRAFT_DEFAULT_PROMPT")
    ]
    edit_prompt = source[
        source.index("EDIT_PROJECT_CONTEXT_DRAFT_DEFAULT_PROMPT"):
        source.index("SERVICE_PROMPT_DEFAULTS")
    ]

    assert '"activation_description"' in create_prompt
    assert "Maximum length: 300 characters" in create_prompt
    assert '"activation_description"' in edit_prompt
    assert "max 300 characters" in edit_prompt
