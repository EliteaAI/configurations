"""#6826: environment-tolerant model identity derived from the deployment name.

The table covers DIAL, Vertex, Bedrock, Gemini and Foundry deployment names; the
fixture CSV is one such environment's model list (names only).
"""
import csv
import importlib
import pathlib
import sys
import types

import pytest
from pydantic import ValidationError

ROOT = pathlib.Path(__file__).resolve().parents[2]
FIXTURE = ROOT / 'tests/fixtures/model_limits_6826.csv'
PACKAGE = 'configurations_6826_identity'
CREDS = {'elitea_title': 'creds', 'private': False}


@pytest.fixture(scope='module')
def package():
    module = types.ModuleType(PACKAGE)
    module.__path__ = [str(ROOT)]
    sys.modules[PACKAGE] = module
    yield PACKAGE
    for name in [m for m in sys.modules if m.startswith(PACKAGE)]:
        del sys.modules[name]


@pytest.fixture(scope='module')
def identity(package):
    return importlib.import_module(f'{package}.llm_model_identity')


@pytest.fixture(scope='module')
def llm_model(package):
    return importlib.import_module(f'{package}.models.pd.llm_model')


# name, canonical, contract, low_tier_hint, preview
CHAT_TABLE = [
    ('gpt-5.6-luna-2026-07-09', 'openai/gpt-5-6-luna', 'default', True, False),
    ('gpt-5.6-luna-2026-07-09-reasoning', 'openai/gpt-5-6-luna', 'reasoning', True, False),
    ('global.openai.gpt-5.6-luna', 'openai/gpt-5-6-luna', 'default', True, False),
    ('gpt-5.4-2026-03-05', 'openai/gpt-5-4', 'default', False, False),
    ('1_gpt-5.4', 'openai/gpt-5-4', 'default', False, False),
    ('gpt-5.4-mini-2026-03-17', 'openai/gpt-5-4-mini', 'default', True, False),
    ('gpt-5.6-sol-2026-07-09', 'openai/gpt-5-6-sol', 'default', False, False),
    ('o3-mini-2025-01-31-reasoning', 'openai/o3-mini', 'reasoning', True, False),
    ('gpt-chat-latest', 'openai/gpt-chat', 'default', False, False),
    ('claude-haiku-4-5@20251001', 'anthropic/claude-haiku-4-5', 'default', True, False),
    ('claude-haiku-4-5@20251001-websearch', 'anthropic/claude-haiku-4-5', 'websearch', True, False),
    ('claude-haiku-5-5@default', 'anthropic/claude-haiku-5-5', 'default', True, False),
    ('claude-opus-5-5@default', 'anthropic/claude-opus-5-5', 'default', False, False),
    ('eu.anthropic.claude-haiku-4-5-20251001-v1:0', 'anthropic/claude-haiku-4-5', 'default', True, False),
    ('anthropic.claude-haiku-4-5-20251001-v1:0-with-thinking', 'anthropic/claude-haiku-4-5', 'reasoning', True, False),
    ('anthropic.claude-sonnet-4-6', 'anthropic/claude-sonnet-4-6', 'default', False, False),
    ('eu.anthropic.claude-sonnet-4-6', 'anthropic/claude-sonnet-4-6', 'default', False, False),
    ('anthropic.claude-sonnet-5-without-thinking', 'anthropic/claude-sonnet-5', 'default', False, False),
    ('anthropic.claude-opus-4-6-v1', 'anthropic/claude-opus-4-6', 'default', False, False),
    ('anthropic.claude-v3-haiku', 'anthropic/claude-3-haiku', 'default', True, False),
    ('gemini-3.5-flash', 'google/gemini-3-5-flash', 'default', True, False),
    ('gemini-3.5-flash-with-thinking', 'google/gemini-3-5-flash', 'reasoning', True, False),
    ('gemini-3-flash-preview-google-search', 'google/gemini-3-flash', 'websearch', True, True),
    ('gemini-2.5-flash-lite', 'google/gemini-2-5-flash-lite', 'default', True, False),
    ('gemini-3.1-pro-preview-with-thinking', 'google/gemini-3-1-pro', 'reasoning', False, True),
    ('Foundry.FW-GLM-5.3-Flash', 'zai/glm-5-3-flash', 'default', True, False),
    ('DeepSeek-V4-Flash-2026-04-23', 'deepseek/deepseek-v4-flash', 'default', True, False),
    ('meta.llama4-scout-17b-instruct-v1:0', 'meta/llama4-scout-17b-instruct', 'default', False, False),
    ('qwen.qwen3-coder-480b-a35b-v1:0', 'qwen/qwen3-coder-480b-a35b', 'default', False, False),
    ('xai.grok-4.6', 'xai/grok-4-6', 'default', False, False),
    ('grok-4.3', 'xai/grok-4-3', 'default', False, False),
    ('moonshotai.kimi-k2.5', 'moonshot/kimi-k2-5', 'default', False, False),
    ('nvidia.nemotron-nano-12b-v2', 'nvidia/nemotron-nano-12b-v2', 'default', True, False),
]

NON_CHAT_TABLE = [
    ('text-embedding-3-small-1', 'embedding'),
    ('gemini-embedding-2', 'embedding'),
    ('azure-ai-vision-embeddings', 'embedding'),
    ('gpt-image-2-2026-04-21', 'image'),
    ('gemini-3.1-flash-image', 'image'),
    ('stability.sd3-5-large-v1:0', 'image'),
    ('tts-hd-001', 'audio'),
    ('whisper-001', 'audio'),
    ('gpt-4o-transcribe', 'audio'),
    ('veo-3.1-generate-001', 'video'),
]


@pytest.mark.parametrize('name,canonical,contract,low_tier_hint,preview', CHAT_TABLE)
def test_plan_table_chat_rows(identity, name, canonical, contract, low_tier_hint, preview):
    result = identity.resolve_identity(name)
    vendor, family = canonical.split('/')
    assert result == {
        'version': 1, 'canonical': canonical, 'vendor': vendor, 'family': family, 'kind': 'chat',
        'contract': contract, 'low_tier_hint': low_tier_hint, 'preview': preview, 'source': 'rule',
    }
    assert identity.CANONICAL_RE.fullmatch(canonical)


@pytest.mark.parametrize('name,kind', NON_CHAT_TABLE)
def test_plan_table_non_chat_rows(identity, name, kind):
    result = identity.resolve_identity(name)
    assert result['kind'] == kind
    assert result['canonical'] is None
    assert result['low_tier_hint'] is False


def _fixture_names():
    with FIXTURE.open(newline='') as handle:
        return [row['Model ID'] for row in csv.DictReader(handle)]


def test_whole_fixture_invariant(identity):
    names = _fixture_names()
    assert len(names) == 172
    chat = 0
    for name in names:
        result = identity.resolve_identity(name)
        if result['kind'] == 'chat':
            chat += 1
            assert result['canonical'] is not None, name
            assert result['vendor'] in identity.VENDORS, name
            assert result['source'] == 'rule', name
            assert identity.CANONICAL_RE.fullmatch(result['canonical']), name
        else:
            assert result['canonical'] is None, name
    assert chat > 100


def test_explicit_override_wins_and_keeps_contract_from_suffix(identity):
    result = identity.resolve_identity('team-router-small-reasoning', explicit='openai/gpt-5-6-luna')
    assert result == {
        'version': 1, 'canonical': 'openai/gpt-5-6-luna', 'vendor': 'openai', 'family': 'gpt-5-6-luna',
        'kind': 'chat', 'contract': 'reasoning', 'low_tier_hint': True, 'preview': False, 'source': 'explicit',
    }


def test_explicit_override_makes_any_name_chat(identity):
    result = identity.resolve_identity('house-embedding-proxy', explicit='anthropic/claude-haiku-4-5')
    assert (result['kind'], result['canonical'], result['source']) == ('chat', 'anthropic/claude-haiku-4-5', 'explicit')


@pytest.mark.parametrize('explicit', ['', 'OpenAI/gpt-5', 'openai', 'acme/model-x', 'openai/-x', 7])
def test_invalid_explicit_override_is_ignored(identity, explicit):
    result = identity.resolve_identity('gpt-5.4-2026-03-05', explicit=explicit)
    assert (result['canonical'], result['source']) == ('openai/gpt-5-4', 'rule')


def test_unresolved_name(identity):
    result = identity.resolve_identity('team-router-small')
    assert result['canonical'] is None and result['vendor'] is None
    assert (result['kind'], result['source'], result['family']) == ('chat', 'unresolved', 'team-router-small')


def test_anthropic_protocol_supplies_vendor_last(identity):
    result = identity.resolve_identity('team-router-small', api_protocol='anthropic')
    assert (result['canonical'], result['source']) == ('anthropic/team-router-small', 'rule')
    assert identity.resolve_identity('gpt-5.4', api_protocol='anthropic')['vendor'] == 'openai'
    assert identity.resolve_identity('team-router-small', api_protocol='azure')['canonical'] is None


@pytest.mark.parametrize('name', ['vertex_ai/claude-haiku-4-5@20251001', 'bedrock/eu.anthropic.claude-haiku-4-5-20251001-v1:0',
                                  'responses/openai/claude-haiku-4-5', '  Claude-Haiku-4.5  '])
def test_provider_paths_and_case(identity, name):
    assert identity.resolve_identity(name)['canonical'] == 'anthropic/claude-haiku-4-5'


@pytest.mark.parametrize('name', [None, '', '   ', 42, '@@@'])
def test_unusable_names_are_unknown(identity, name):
    result = identity.resolve_identity(name)
    assert result['canonical'] is None and result['source'] == 'unresolved'
    assert result['kind'] == 'unknown'


@pytest.mark.parametrize('item,expected', [
    ({'low_tier': True, 'identity': {'low_tier_hint': False}}, True),
    ({'low_tier': False, 'identity': {'low_tier_hint': True}}, True),
    ({'low_tier': None, 'identity': None}, False),
    ({}, False),
    ({'low_tier': 'true', 'identity': {'low_tier_hint': 'yes'}}, False),
])
def test_is_low_tier(identity, item, expected):
    assert identity.is_low_tier(item) is expected


def test_llm_model_canonical_override_field(llm_model):
    model = llm_model.LlmModel(name='team-router', ai_credentials=CREDS)
    assert model.canonical_model is None
    for blank in ('', '   '):
        assert llm_model.LlmModel(name='m', canonical_model=blank, ai_credentials=CREDS).canonical_model is None
    model = llm_model.LlmModel(name='m', canonical_model=' openai/gpt-5-6-luna ', ai_credentials=CREDS)
    assert model.canonical_model == 'openai/gpt-5-6-luna'


@pytest.mark.parametrize('value', ['OpenAI/gpt-5', 'gpt-5', 'acme/model', 'openai/', 'openai/gpt 5', 'a' * 600])
def test_llm_model_canonical_override_rejects_bad_grammar(llm_model, value):
    with pytest.raises(ValidationError, match='canonical_model'):
        llm_model.LlmModel(name='m', canonical_model=value, ai_credentials=CREDS)


def test_llm_model_list_carries_identity(llm_model):
    item = llm_model.LlmModelList(name='m', display_name='M', project_id=2)
    assert item.identity is None
    assert 'identity' in item.model_dump()


def test_picker_items_carry_identity(package, monkeypatch):
    stubs = {
        '.common_utils': {'get_public_project_id': lambda: 1},
        '.folder_access': {'folder_exclusion_clause': lambda *a, **k: None},
        '.local_tools': {'db': None, 'VaultClient': None},
        '.models.configuration': {'Configuration': types.SimpleNamespace(id=None, shared=None)},
        '.utils': {name: None for name in (
            'get_configuration_llm_models_with_limits_query', 'get_embedding_model_query', 'get_vector_storage_query',
            'get_image_generation_model_query', 'get_asr_model_query', 'get_tts_model_query')},
    }
    for suffix, values in stubs.items():
        module = types.ModuleType(package + suffix)
        module.__dict__.update(values)
        monkeypatch.setitem(sys.modules, module.__name__, module)
    utils_models = importlib.import_module(f'{package}.utils_models')
    handler = utils_models.LLMModelHandler(7, 1)
    row = types.SimpleNamespace(name='team-router', display_name='Team router', project_id=7, shared=False,
                                api_protocol='azure', canonical_model='openai/gpt-5-6-luna')
    data = handler.validate_and_convert_model(row)
    assert data['identity']['canonical'] == 'openai/gpt-5-6-luna' and data['identity']['source'] == 'explicit'
    assert 'canonical_model' not in data
    data = handler.validate_and_convert_model(types.SimpleNamespace(
        name='claude-haiku-4-5@20251001', display_name='Haiku', project_id=1, shared=True, api_protocol='azure'))
    assert data['identity']['canonical'] == 'anthropic/claude-haiku-4-5'
