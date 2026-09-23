"""#6713: the azure+reasoning rejection applies to DIAL credentials only.

The credential type is resolved lazily through the validation context, so the
lookup must only happen when the model would otherwise be rejected.
"""
import importlib.util
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[2]

_spec = importlib.util.spec_from_file_location('llm_model_6713', ROOT / 'models/pd/llm_model.py')
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
LlmModel = _module.LlmModel

CREDS = {'elitea_title': 'dial_creds', 'private': False}


def _data(**overrides):
    return {'name': 'gpt-5.4', 'ai_credentials': CREDS, **overrides}


def _context(credential_type):
    resolver = MagicMock(return_value=credential_type)
    return {'resolve_ai_credential_type': resolver}, resolver


def test_api_protocol_defaults_to_null():
    # null means "not chosen" - persisted non-DIAL models no longer carry a meaningless 'azure'
    assert LlmModel.model_validate(_data()).api_protocol is None
    assert LlmModel.model_validate(_data()).model_dump()['api_protocol'] is None


def test_azure_reasoning_rejected_for_dial_credential():
    context, resolver = _context('ai_dial')
    with pytest.raises(ValidationError, match="does not support reasoning"):
        LlmModel.model_validate(_data(api_protocol='azure', supports_reasoning=True), context=context)
    resolver.assert_called_once()
    assert resolver.call_args.args[0].elitea_title == 'dial_creds'


@pytest.mark.parametrize('credential_type', ['amazon_bedrock', 'azure_open_ai', 'open_ai', None])
def test_azure_reasoning_allowed_for_non_dial_or_unresolved_credential(credential_type):
    # None covers a deleted/inaccessible credential: without proof it is DIAL we do not reject
    context, _ = _context(credential_type)
    model = LlmModel.model_validate(_data(api_protocol='azure', supports_reasoning=True), context=context)
    assert model.api_protocol == 'azure'


def test_azure_reasoning_allowed_without_resolver_context():
    # direct model_validate callers (no context) cannot prove the credential is DIAL
    assert LlmModel.model_validate(_data(api_protocol='azure', supports_reasoning=True)).supports_reasoning


@pytest.mark.parametrize('overrides', [
    {'api_protocol': 'azure', 'supports_reasoning': False},
    {'api_protocol': None, 'supports_reasoning': True},
    {'supports_reasoning': True},
    {'api_protocol': 'openai', 'supports_reasoning': True},
    {'api_protocol': 'anthropic', 'supports_reasoning': True},
])
def test_resolver_not_called_when_rule_cannot_fire(overrides):
    # the lookup is a DB hit - it must only run on the azure+reasoning path
    context, resolver = _context('ai_dial')
    LlmModel.model_validate(_data(**overrides), context=context)
    resolver.assert_not_called()


def test_invalid_protocol_still_rejected():
    with pytest.raises(ValidationError):
        LlmModel.model_validate(_data(api_protocol='bedrock'))
