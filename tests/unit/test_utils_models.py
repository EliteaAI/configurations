"""ModelConfigurationService default resolution, run against the real utils_models module (#7437)."""
import importlib.util
import pathlib
import sys
import types

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
PACKAGE = 'configurations_utils_models_test'
PUBLIC = 1
PROJECT = 65


class FakeVault:
    """Per-project vault double that counts reads, so tests can pin how often each vault is touched."""

    secrets = {}
    reads = []

    def __init__(self, project_id):
        self.project_id = project_id

    @classmethod
    def from_project(cls, project_id):
        return cls(project_id)

    def get_secrets(self):
        FakeVault.reads.append(('get_secrets', self.project_id))
        return dict(FakeVault.secrets.get(self.project_id, {}))

    def get_all_secrets(self):
        FakeVault.reads.append(('get_all_secrets', self.project_id))
        return dict(FakeVault.secrets.get(self.project_id, {}))


def _stub(name, **attrs):
    module = types.ModuleType(f'{PACKAGE}.{name}')
    module.__dict__.update(attrs)
    sys.modules[module.__name__] = module


@pytest.fixture(scope='module')
def utils_models():
    for suffix, path in [('', ROOT), ('.models', ROOT / 'models'), ('.models.pd', ROOT / 'models/pd')]:
        package = types.ModuleType(PACKAGE + suffix)
        package.__path__ = [str(path)]
        sys.modules[package.__name__] = package
    _stub('common_utils', get_public_project_id=lambda: PUBLIC)
    _stub('folder_access', folder_exclusion_clause=lambda *_: None)
    _stub('llm_model_identity', resolve_identity=lambda *_, **__: None)
    _stub('local_tools', db=None, VaultClient=FakeVault)
    _stub('models.configuration', Configuration=types.SimpleNamespace(id=None, shared=None))
    _stub('models.pd.llm_model', LlmModelList=None, EmbeddingModelList=None, VectorStorageModelList=None,
          ImageGenerationModelList=None, ASRModelList=None, TTSModelList=None)
    _stub('utils', get_configuration_llm_models_with_limits_query=None, get_embedding_model_query=None,
          get_vector_storage_query=None, get_image_generation_model_query=None, get_asr_model_query=None,
          get_tts_model_query=None)
    spec = importlib.util.spec_from_file_location(f'{PACKAGE}.utils_models', ROOT / 'utils_models.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    yield module
    for name in [m for m in sys.modules if m.startswith(PACKAGE)]:
        del sys.modules[name]


def model(name, project_id, shared, low=False, high=False):
    return {'name': name, 'display_name': name, 'project_id': project_id, 'shared': shared,
            'low_tier': low, 'high_tier': high}


def stage2_models():
    """Project 65 after the unshare: its own broken GPT-5.4 inserted first, then the remaining shared models."""
    return {
        (PROJECT, 'GPT-5.4'): model('GPT-5.4', PROJECT, False),
        (PUBLIC, 'claude-haiku-5-5'): model('claude-haiku-5-5', PUBLIC, True),
        (PUBLIC, 'claude-sonnet-5-5'): model('claude-sonnet-5-5', PUBLIC, True, high=True),
        (PUBLIC, 'gpt-5-mini'): model('gpt-5-mini', PUBLIC, True, low=True),
    }


def pointer(section, name, project_id):
    return {f'default_{section}_model_name': name, f'default_{section}_model_project_id': project_id}


def resolve(utils_models, items, own=None, public=None, project_id=PROJECT, section='llm'):
    FakeVault.secrets = {project_id: own or {}, PUBLIC: public or {}} if project_id != PUBLIC else {PUBLIC: own or {}}
    FakeVault.reads = []
    service = utils_models.ModelConfigurationService(project_id)
    service.get_available_models = lambda *_: {key: dict(value) for key, value in items.items()}
    response, status = service.get_models(section, include_shared=True)
    assert status == 200
    return response


def chosen(response):
    return (response['default_model_name'], response['low_tier_default_model_name'],
            response['high_tier_default_model_name'])


UNSHARED_PUBLIC_POINTERS = {
    **pointer('llm', 'gpt-5.4-2026-03-05', 1),
    **pointer('llm_high_tier', 'gpt-5.6-terra-2026-07-09', 1),
}


def test_valid_own_private_default_is_kept(utils_models):
    response = resolve(utils_models, stage2_models(), own=pointer('llm', 'GPT-5.4', PROJECT))
    assert response['default_model_name'] == 'GPT-5.4'
    assert response['default_model_project_id'] == PROJECT


def test_stale_own_default_falls_back_to_public_default(utils_models):
    response = resolve(utils_models, stage2_models(), own=pointer('llm', 'gone', PROJECT),
                       public=pointer('llm', 'claude-sonnet-5-5', PUBLIC))
    assert (response['default_model_name'], response['default_model_project_id']) == ('claude-sonnet-5-5', PUBLIC)


def test_stage2_unshared_public_default_falls_to_the_low_tier_model(utils_models):
    response = resolve(utils_models, stage2_models(), public=UNSHARED_PUBLIC_POINTERS)
    assert chosen(response) == ('gpt-5-mini', 'gpt-5-mini', 'claude-sonnet-5-5')
    assert response['default_model_project_id'] == PUBLIC


def test_stage2_without_low_tier_models_falls_to_first_shared_in_display_order(utils_models):
    items = stage2_models()
    items[(PUBLIC, 'gpt-5-mini')]['low_tier'] = False
    response = resolve(utils_models, items, public=UNSHARED_PUBLIC_POINTERS)
    assert chosen(response)[:2] == ('claude-haiku-5-5', '')


def test_public_low_tier_pointer_is_the_default_fallback(utils_models):
    items = stage2_models()
    items[(PUBLIC, 'claude-haiku-5-5')]['low_tier'] = True
    response = resolve(utils_models, items,
                       public={**UNSHARED_PUBLIC_POINTERS, **pointer('llm_low_tier', 'gpt-5-mini', PUBLIC)})
    assert chosen(response)[:2] == ('gpt-5-mini', 'gpt-5-mini')


def test_private_models_only_resolve_in_display_order(utils_models):
    items = {
        (PROJECT, 'zeta'): model('zeta', PROJECT, False),
        (PROJECT, 'alpha'): model('alpha', PROJECT, False),
    }
    assert resolve(utils_models, items)['default_model_name'] == 'alpha'


def test_equal_display_names_break_ties_by_project_then_name(utils_models):
    items = {
        (7, 'b-model'): {**model('b-model', 7, True), 'display_name': 'Same'},
        (PUBLIC, 'z-model'): {**model('z-model', PUBLIC, True), 'display_name': 'Same'},
        (PUBLIC, 'a-model'): {**model('a-model', PUBLIC, True), 'display_name': 'Same'},
    }
    response = resolve(utils_models, items)
    assert [item['name'] for item in response['items']] == ['a-model', 'z-model', 'b-model']
    assert response['default_model_name'] == 'a-model'
    assert response['items'][0]['default'] is True


def test_tier_pointer_at_a_model_without_the_flag_moves_on(utils_models):
    response = resolve(utils_models, stage2_models(),
                       own=pointer('llm_high_tier', 'claude-haiku-5-5', PUBLIC),
                       public=pointer('llm_high_tier', 'claude-sonnet-5-5', PUBLIC))
    assert response['high_tier_default_model_name'] == 'claude-sonnet-5-5'


def test_no_tier_flags_leave_tiers_blank(utils_models):
    items = {key: {**value, 'low_tier': False, 'high_tier': False} for key, value in stage2_models().items()}
    response = resolve(utils_models, items, own=pointer('llm', 'GPT-5.4', PROJECT))
    assert chosen(response) == ('GPT-5.4', '', '')
    assert response['low_tier_default_model_project_id'] == ''


def test_public_project_keeps_its_own_unshared_default(utils_models):
    items = {
        (PUBLIC, 'gpt-5.4-2026-03-05'): model('gpt-5.4-2026-03-05', PUBLIC, False),
        (PUBLIC, 'claude-sonnet-5-5'): model('claude-sonnet-5-5', PUBLIC, True),
    }
    response = resolve(utils_models, items, own=pointer('llm', 'gpt-5.4-2026-03-05', PUBLIC), project_id=PUBLIC)
    assert response['default_model_name'] == 'gpt-5.4-2026-03-05'


def test_valid_own_pointers_never_read_the_public_vault(utils_models):
    own = {**pointer('llm', 'GPT-5.4', PROJECT), **pointer('llm_low_tier', 'gpt-5-mini', PUBLIC),
           **pointer('llm_high_tier', 'claude-sonnet-5-5', PUBLIC)}
    resolve(utils_models, stage2_models(), own=own)
    assert FakeVault.reads == [('get_secrets', PROJECT)]


def test_each_vault_is_read_at_most_once(utils_models):
    resolve(utils_models, stage2_models(), public=UNSHARED_PUBLIC_POINTERS)
    assert FakeVault.reads == [('get_secrets', PROJECT), ('get_all_secrets', PUBLIC)]


def test_non_llm_sections_keep_first_available_fallback(utils_models):
    items = {
        (PROJECT, 'own-pgvector'): {'name': 'own-pgvector', 'shared': False},
        (PUBLIC, 'shared-pgvector'): {'name': 'shared-pgvector', 'shared': True},
    }
    response = resolve(utils_models, items, section='vectorstorage')
    assert (response['default_model_name'], response['default_model_project_id']) == ('own-pgvector', PROJECT)
    assert [item['name'] for item in response['items']] == ['shared-pgvector', 'own-pgvector']
    assert 'low_tier_default_model_name' not in response
