"""The application draft prompts take the Chat Starters and agent skill limits from placeholders, and the
migration moves seeded rows off the past defaults without touching customized ones."""

import importlib
import importlib.util
import pathlib
import string
import sys
import types

import pytest


ROOT = pathlib.Path(__file__).resolve().parents[2]
PACKAGE = 'configurations_application_draft'
KEYS = ('edit_application_draft', 'generate_application_draft')
RESOURCE_SLOTS = {'toolkits', 'mcp', 'agents', 'pipelines', 'skills'}


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


def _slots(prompt):
    return {name for _, name, _, _ in string.Formatter().parse(prompt) if name is not None}


def test_generate_prompt_slots(defaults):
    prompt = defaults.SERVICE_PROMPT_DEFAULTS['generate_application_draft']
    assert _slots(prompt) == RESOURCE_SLOTS | {'max_conversation_starters', 'max_skills_per_agent'}


def test_edit_prompt_slots(defaults):
    prompt = defaults.SERVICE_PROMPT_DEFAULTS['edit_application_draft']
    assert _slots(prompt) == RESOURCE_SLOTS | {'max_conversation_starters', 'max_skills_per_agent', 'current_config'}


@pytest.mark.parametrize('key', KEYS)
def test_no_hardcoded_starters_limit_remains(defaults, key):
    prompt = defaults.SERVICE_PROMPT_DEFAULTS[key]
    assert 'max 4' not in prompt and '1–4' not in prompt


@pytest.mark.parametrize('key', KEYS)
def test_no_hardcoded_skills_limit_remains(defaults, key):
    prompt = defaults.SERVICE_PROMPT_DEFAULTS[key]
    assert 'max 5' not in prompt and 'Maximum 5' not in prompt


@pytest.mark.parametrize('key', KEYS)
def test_prompt_renders_the_configured_limit(defaults, key):
    values = {slot: '' for slot in RESOURCE_SLOTS}
    values.update(current_config='{}', max_conversation_starters=7, max_skills_per_agent=37)
    rendered = defaults.SERVICE_PROMPT_DEFAULTS[key].format(**values)
    assert 'max 7 items' in rendered and '{max_conversation_starters}' not in rendered
    assert ('Maximum 37 skills.' in rendered or '(max 37)' in rendered) and '{max_skills_per_agent}' not in rendered


def test_past_defaults_are_frozen_and_differ_from_current(defaults):
    past = defaults.APPLICATION_DRAFT_PAST_DEFAULTS
    assert set(past) == set(KEYS)
    assert len(past['generate_application_draft']) == 6
    assert len(past['edit_application_draft']) == 4
    for key, prompts in past.items():
        assert len(set(prompts)) == len(prompts)
        assert defaults.SERVICE_PROMPT_DEFAULTS[key].strip() not in {p.strip() for p in prompts}
        assert all('{max_skills_per_agent}' not in p for p in prompts)


@pytest.mark.parametrize('key', KEYS)
def test_latest_past_default_differs_only_by_the_skills_limit_and_shape(defaults, key):
    latest = defaults.APPLICATION_DRAFT_PAST_DEFAULTS[key][-1]
    current = defaults.SERVICE_PROMPT_DEFAULTS[key]
    skill_entry = '"id": <int>, "name": "<str>"}}'
    assert current.count(skill_entry) == 1
    previous = current.replace('{max_skills_per_agent}', '5').replace(
        skill_entry, '"id": <int>, "name": "<str>", "description": "<str or null>"}}')
    assert previous == latest


@pytest.mark.parametrize('key', KEYS)
def test_suggested_skills_shape_omits_the_description(defaults, key):
    prompt = defaults.SERVICE_PROMPT_DEFAULTS[key]
    skills_entry = prompt[prompt.index('"suggested_skills": ['):]
    skills_entry = skills_entry[:skills_entry.index(']') + 1]
    assert 'description' not in skills_entry and '"id": <int>' in skills_entry


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
    return method.migrate_service_prompt_application_draft(param=param)


def _defaults(task_module):
    return importlib.import_module(f'{PACKAGE}_task.models.pd.service_prompt_defaults')


def _past(task_module, key, index):
    return _defaults(task_module).APPLICATION_DRAFT_PAST_DEFAULTS[key][index]


@pytest.mark.parametrize('index', [0, -1])
def test_live_run_upgrades_past_defaults(task_module, index):
    rows = [_Row(i, key, _past(task_module, key, index)) for i, key in enumerate(KEYS)]
    session = _FakeSession(rows)
    result = _run(task_module, session)
    current = _defaults(task_module).SERVICE_PROMPT_DEFAULTS
    assert result['migrated'] == sorted(KEYS) and result['dry_run'] is False
    for row in rows:
        assert row.data == {'key': row.elitea_title, 'prompt': current[row.elitea_title], 'extra': 'keep'}
    assert session.commits == 1


def test_past_default_with_surrounding_whitespace_is_migrated(task_module):
    key = 'generate_application_draft'
    rows = [_Row(1, key, '\n  ' + _past(task_module, key, 2) + '  \n')]
    result = _run(task_module, _FakeSession(rows))
    assert result['migrated'] == [key]


def test_dry_run_reports_without_writing(task_module):
    rows = [_Row(i, key, _past(task_module, key, 0)) for i, key in enumerate(KEYS)]
    session = _FakeSession(rows)
    result = _run(task_module, session, 'dry_run')
    assert result['migrated'] == sorted(KEYS) and result['dry_run'] is True
    assert all(row.data['prompt'] == _past(task_module, row.elitea_title, 0) for row in rows)
    assert session.commits == 0


def test_customized_prompt_is_skipped(task_module):
    rows = [_Row(1, 'generate_application_draft', 'my own prompt'),
            _Row(2, 'edit_application_draft', _past(task_module, 'edit_application_draft', 1))]
    result = _run(task_module, _FakeSession(rows))
    assert result['skipped_custom'] == ['generate_application_draft']
    assert result['migrated'] == ['edit_application_draft']
    assert rows[0].data['prompt'] == 'my own prompt'


def test_rerun_is_up_to_date(task_module):
    current = _defaults(task_module).SERVICE_PROMPT_DEFAULTS
    rows = [_Row(i, key, current[key]) for i, key in enumerate(KEYS)]
    session = _FakeSession(rows)
    result = _run(task_module, session)
    assert result['migrated'] == [] and result['up_to_date'] == sorted(KEYS)
    assert session.commits == 0


def test_missing_rows_are_reported(task_module):
    result = _run(task_module, _FakeSession([]))
    assert result['migrated'] == [] and result['missing'] == sorted(KEYS)
