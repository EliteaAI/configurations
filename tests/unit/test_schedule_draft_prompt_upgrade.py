"""The pipeline authoring prompt migration upgrades an untouched schedule draft prompt to the
toolkit index slot, and warns about an edited one that keeps no slot."""

import importlib
import pathlib
import sys
import types

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
PACKAGE = "configurations_schedule_draft_task"
KEY = "generate_schedule_draft"
SLOT = "{toolkit_indexes}"


class _Row:
    def __init__(self, id_, title, prompt):
        self.id = id_
        self.elitea_title = title
        self.data = {"key": title, "prompt": prompt, "extra": "keep"}


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


def _stub(name, attrs):
    module = types.ModuleType(f"{PACKAGE}.{name}")
    module.__dict__.update(attrs)
    return module


@pytest.fixture
def task_module():
    for suffix, path in [
        ("", ROOT),
        (".models", ROOT / "models"),
        (".models.pd", ROOT / "models/pd"),
        (".methods", ROOT / "methods"),
    ]:
        module = types.ModuleType(PACKAGE + suffix)
        module.__path__ = [str(path)]
        sys.modules[module.__name__] = module
    sys.modules[f"{PACKAGE}.common_utils"] = _stub(
        "common_utils", {"get_public_project_id": lambda: 1}
    )
    sys.modules[f"{PACKAGE}.models.configuration"] = _stub(
        "models.configuration", {"Configuration": object}
    )
    module = importlib.import_module(f"{PACKAGE}.methods.admin_tasks")
    module.flag_modified = lambda obj, attr: None
    yield module
    for name in [m for m in sys.modules if m.startswith(PACKAGE)]:
        del sys.modules[name]


class _Log:
    def __init__(self):
        self.warnings = []

    def warning(self, message, *args):
        self.warnings.append(message % args if args else message)

    def info(self, message, *args):
        pass

    def error(self, message, *args):
        pass

    def exception(self, message, *args):
        pass


def _defaults():
    return importlib.import_module(f"{PACKAGE}.models.pd.service_prompt_defaults")


def _run(task_module, session, param=""):
    task_module.db = types.SimpleNamespace(get_session=lambda project_id: session)
    task_module.log = _Log()
    method = task_module.Method.__new__(task_module.Method)
    return method.migrate_service_prompt_pipeline_authoring(
        param=param
    ), task_module.log


def test_untouched_v1_row_is_upgraded_to_the_slot_text(task_module):
    defaults = _defaults()
    row = _Row(1, KEY, defaults.GENERATE_SCHEDULE_DRAFT_DEFAULT_PROMPT_V1)
    session = _FakeSession([row])
    result, log = _run(task_module, session)
    assert result["migrated"] == [KEY] and result["skipped_custom"] == []
    assert SLOT in row.data["prompt"] and row.data["extra"] == "keep"
    assert session.commits == 1
    assert log.warnings == []


def test_edited_row_without_the_slot_is_kept_and_warned_about(task_module):
    row = _Row(7, KEY, "my own schedule prompt")
    result, log = _run(task_module, _FakeSession([row]))
    assert result["skipped_custom"] == [KEY] and result["migrated"] == []
    assert row.data["prompt"] == "my own schedule prompt"
    slot_warnings = [w for w in log.warnings if SLOT in w and "id=7" in w]
    assert len(slot_warnings) == 1
    assert "no toolkit indexes" in slot_warnings[0]


def test_edited_row_with_the_slot_gets_only_the_custom_warning(task_module):
    row = _Row(8, KEY, f"my own prompt with {SLOT}")
    result, log = _run(task_module, _FakeSession([row]))
    assert result["skipped_custom"] == [KEY]
    assert len(log.warnings) == 1
    assert "customized" in log.warnings[0]


def test_dry_run_warns_and_writes_nothing(task_module):
    row = _Row(9, KEY, "my own schedule prompt")
    session = _FakeSession([row])
    result, log = _run(task_module, session, "dry_run")
    assert result["dry_run"] is True and result["skipped_custom"] == [KEY]
    assert any(SLOT in w and w.startswith("[DRY RUN] ") for w in log.warnings)
    assert session.commits == 0
