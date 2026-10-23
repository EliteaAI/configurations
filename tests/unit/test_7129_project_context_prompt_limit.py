"""Project Context prompts ask for a 2400-char background (hard cap stays 2500), and the migration
moves seeded rows off the past defaults without touching customized ones (#7129)."""

import importlib
import importlib.util
import pathlib
import sys
import types

import pytest


ROOT = pathlib.Path(__file__).resolve().parents[2]
PACKAGE = 'configurations_7129'
KEYS = ('project_context_generator', 'edit_project_context_draft')


@pytest.fixture(scope='module')
def defaults():
    package = types.ModuleType(PACKAGE)
    package.__path__ = [str(ROOT / 'models/pd')]
    sys.modules[PACKAGE] = package
    try:
        for name in ('service_prompt_keys', 'service_prompt_defaults'):
            full = f'{PACKAGE}.{name}'
            spec = importlib.util.spec_from_file_location(full, ROOT / f'models/pd/{name}.py')
            module = importlib.util.module_from_spec(spec)
            sys.modules[full] = module
            spec.loader.exec_module(module)
        yield sys.modules[f'{PACKAGE}.service_prompt_defaults']
    finally:
        for name in [m for m in sys.modules if m.startswith(PACKAGE)]:
            del sys.modules[name]


def _current(defaults, key):
    return defaults.SERVICE_PROMPT_DEFAULTS[key]


def test_current_defaults_ask_for_2400_characters(defaults):
    generator = _current(defaults, 'project_context_generator')
    edit = _current(defaults, 'edit_project_context_draft')
    assert '2500' not in generator and '2500' not in edit
    assert 'Maximum length: 2400 characters.' in generator
    assert 'max 2400 characters' in edit
    assert 'stay within 2400 characters' in edit


def test_activation_description_limit_is_unchanged(defaults):
    assert 'Maximum length: 300 characters.' in _current(defaults, 'project_context_generator')
    assert 'max 300 characters' in _current(defaults, 'edit_project_context_draft')


def test_hard_cap_is_unchanged():
    spec = importlib.util.spec_from_file_location('project_context_7129', ROOT / 'models/pd/project_context.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.PROJECT_CONTEXT_MAX_LEN == 2500


def test_edit_default_still_formats_with_current_config(defaults):
    rendered = _current(defaults, 'edit_project_context_draft').format(current_config='{"a": 1}')
    assert '"project_background"' in rendered and '{current_config}' not in rendered


@pytest.mark.parametrize('key', KEYS)
def test_past_defaults_are_frozen_and_differ_from_current(defaults, key):
    past = defaults.PROJECT_CONTEXT_PAST_DEFAULT_PROMPTS[key]
    assert len(past) == 2
    assert all('2500' in prompt for prompt in past)
    assert _current(defaults, key).strip() not in {prompt.strip() for prompt in past}


# --- migration task ------------------------------------------------------------------------

class _Row:
    def __init__(self, id_, title, prompt):
        self.id = id_
        self.elitea_title = title
        self.data = {'key': title, 'prompt': prompt, 'extra': 'keep'}


class _FakeSession:
    def __init__(self, rows):
        self.rows = {row.elitea_title: row for row in rows}
        self.commits = 0

    def query(self, model):
        return self

    def filter_by(self, elitea_title, type):
        self._title = elitea_title
        return self

    def first(self):
        return self.rows.get(self._title)

    def commit(self):
        self.commits += 1

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _stub(name, **attrs):
    module = types.ModuleType(f'{PACKAGE}_task.{name}')
    module.__dict__.update(attrs)
    return module


@pytest.fixture
def task_module():
    package = PACKAGE + '_task'
    for suffix, path in [('', ROOT), ('.models', ROOT / 'models'), ('.models.pd', ROOT / 'models/pd'),
                         ('.methods', ROOT / 'methods')]:
        module = types.ModuleType(package + suffix)
        module.__path__ = [str(path)]
        sys.modules[module.__name__] = module
    sys.modules[f'{package}.common_utils'] = _stub('common_utils', get_public_project_id=lambda: 1)
    sys.modules[f'{package}.models.configuration'] = _stub('models.configuration', Configuration=object)
    module = importlib.import_module(f'{package}.methods.admin_tasks')
    module.flag_modified = lambda obj, attr: None
    yield module
    for name in [m for m in sys.modules if m.startswith(package)]:
        del sys.modules[name]


def _run(task_module, session, param=''):
    task_module.db = types.SimpleNamespace(get_session=lambda project_id: session)
    method = task_module.Method.__new__(task_module.Method)
    return method.migrate_service_prompt_project_context(param=param)


def task_module_defaults(task_module):
    return importlib.import_module(f'{PACKAGE}_task.models.pd.service_prompt_defaults')


def _past(task_module, key, index):
    return task_module_defaults(task_module).PROJECT_CONTEXT_PAST_DEFAULT_PROMPTS[key][index]


@pytest.mark.parametrize('index', [0, 1])
def test_live_run_upgrades_past_defaults(task_module, index):
    rows = [_Row(i, key, _past(task_module, key, index)) for i, key in enumerate(KEYS)]
    session = _FakeSession(rows)
    result = _run(task_module, session)
    defaults = task_module_defaults(task_module).SERVICE_PROMPT_DEFAULTS
    assert result['migrated'] == sorted(KEYS) and result['dry_run'] is False
    for row in rows:
        assert row.data == {'key': row.elitea_title, 'prompt': defaults[row.elitea_title], 'extra': 'keep'}
    assert session.commits == 1


def test_dry_run_reports_without_writing(task_module):
    rows = [_Row(1, key, _past(task_module, key, 1)) for key in KEYS]
    session = _FakeSession(rows)
    result = _run(task_module, session, 'dry_run')
    assert result['migrated'] == sorted(KEYS) and result['dry_run'] is True
    assert all(row.data['prompt'] == _past(task_module, row.elitea_title, 1) for row in rows)
    assert session.commits == 0


def test_customized_prompt_is_skipped(task_module):
    rows = [_Row(1, 'project_context_generator', 'my own prompt'),
            _Row(2, 'edit_project_context_draft', _past(task_module, 'edit_project_context_draft', 1))]
    session = _FakeSession(rows)
    result = _run(task_module, session)
    assert result['skipped_custom'] == ['project_context_generator']
    assert result['migrated'] == ['edit_project_context_draft']
    assert rows[0].data['prompt'] == 'my own prompt'


def test_rerun_is_up_to_date(task_module):
    defaults = task_module_defaults(task_module).SERVICE_PROMPT_DEFAULTS
    rows = [_Row(i, key, defaults[key]) for i, key in enumerate(KEYS)]
    session = _FakeSession(rows)
    result = _run(task_module, session)
    assert result['migrated'] == [] and result['up_to_date'] == sorted(KEYS)
    assert session.commits == 0


def test_missing_rows_are_reported(task_module):
    result = _run(task_module, _FakeSession([]))
    assert result['migrated'] == [] and result['missing'] == sorted(KEYS)
