"""Default Project Context generator prompts must emit the activation classifier."""

import importlib.util
import pathlib


ROOT = pathlib.Path(__file__).resolve().parents[2]


def _load_project_context_model():
    path = ROOT / "models/pd/project_context.py"
    spec = importlib.util.spec_from_file_location("configuration_project_context_5326", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.ProjectContext


def test_project_context_schema_accepts_activation_description():
    project_context = _load_project_context_model()
    schema = project_context.model_json_schema()

    activation_schema = schema["properties"]["activation_description"]
    assert any(option.get("maxLength") == 300 for option in activation_schema["anyOf"])
    parsed = project_context.model_validate({
        "content": "Joke rules",
        "enabled": True,
        "activation_description": "Use for joke requests.",
    })
    assert parsed.activation_description == "Use for joke requests."


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
