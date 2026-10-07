"""api_protocol on LlmModel.

#6713 made it nullable. #6919 dropped the azure+reasoning rejection: azure is DIAL's
chat/completions route, which is the documented route for Gemini, and reasoning works there.
The remaining guidance for Claude/GPT on azure is a UI warning only.
"""
import importlib.util
from pathlib import Path

import pytest
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[2]

_spec = importlib.util.spec_from_file_location('llm_model_protocol', ROOT / 'models/pd/llm_model.py')
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
LlmModel = _module.LlmModel

CREDS = {'elitea_title': 'dial_creds', 'private': False}


def _data(**overrides):
    return {'name': 'gemini-3.8-flash', 'ai_credentials': CREDS, **overrides}


def test_api_protocol_defaults_to_null():
    # null means "not chosen" - persisted non-DIAL models carry no meaningless 'azure'
    assert LlmModel.model_validate(_data()).api_protocol is None
    assert LlmModel.model_validate(_data()).model_dump()['api_protocol'] is None


@pytest.mark.parametrize('api_protocol', ['azure', 'openai', 'anthropic', None])
def test_reasoning_is_accepted_on_every_protocol(api_protocol):
    model = LlmModel.model_validate(_data(api_protocol=api_protocol, supports_reasoning=True))
    assert model.supports_reasoning is True
    assert model.api_protocol == api_protocol


def test_invalid_protocol_still_rejected():
    with pytest.raises(ValidationError):
        LlmModel.model_validate(_data(api_protocol='bedrock'))
