"""Model identity keeps normalizing names itself now that the name profiles are gone (#7209)."""
import importlib
import pathlib
import sys
import types

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
PACKAGE = 'configurations_7209_identity'


@pytest.fixture(scope='module')
def identity():
    module = types.ModuleType(PACKAGE)
    module.__path__ = [str(ROOT)]
    sys.modules[PACKAGE] = module
    yield importlib.import_module(f'{PACKAGE}.llm_model_identity')
    for name in [m for m in sys.modules if m.startswith(PACKAGE)]:
        del sys.modules[name]


@pytest.mark.parametrize('name,normalized', [
    ('GPT_5.6-Luna', 'gpt-5-6-luna'),
    ('claude_sonnet_5', 'claude-sonnet-5'),
    ('gpt-5.2', 'gpt-5-2'),
    (None, ''),
])
def test_names_are_lowercased_with_dots_and_underscores_as_dashes(identity, name, normalized):
    assert identity.normalize_model_name(name) == normalized


def test_dotted_underscored_and_dashed_spellings_share_one_family(identity):
    families = {
        identity.resolve_identity(name)['family']
        for name in ('claude-sonnet-5', 'claude_sonnet_5', 'Claude.Sonnet.5')
    }
    assert families == {'claude-sonnet-5'}
