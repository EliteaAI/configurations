"""Init-time guard that adds configuration.generation to project schemas lacking it (#6943)."""
import importlib.util
import pathlib
import sys
import types
from unittest.mock import MagicMock

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]


def _load():
    spec = importlib.util.spec_from_file_location('configurations_6943_guard.generation_column',
                                                  ROOT / 'generation_column.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class _Session:
    def __init__(self, missing):
        self.missing = missing
        self.executed = []
        self.commit = MagicMock()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, clause, *args, **kwargs):
        sql = str(clause)
        self.executed.append(sql)
        if sql.startswith('SELECT t.table_schema'):
            return [(schema,) for schema in self.missing]
        return None


@pytest.fixture
def guard():
    return _load()


def _db(session):
    return types.SimpleNamespace(get_session=lambda: session)


def test_adds_column_to_every_schema_missing_it(guard):
    session = _Session(['p_2', 'p_7'])
    added = guard.ensure_generation_column(_db(session))
    assert added == ['p_2', 'p_7']
    alters = [s for s in session.executed if s.startswith('ALTER TABLE')]
    assert alters == [
        'ALTER TABLE p_2.configuration ADD COLUMN IF NOT EXISTS generation INTEGER NOT NULL DEFAULT 1',
        'ALTER TABLE p_7.configuration ADD COLUMN IF NOT EXISTS generation INTEGER NOT NULL DEFAULT 1',
    ]
    session.commit.assert_called_once()


def test_noop_when_every_schema_has_the_column(guard):
    session = _Session([])
    assert guard.ensure_generation_column(_db(session)) == []
    assert not [s for s in session.executed if s.startswith('ALTER TABLE')]
    session.commit.assert_not_called()


def test_unexpected_schema_names_are_skipped(guard):
    session = _Session(['p_3', 'public', 'p_x; DROP TABLE y'])
    assert guard.ensure_generation_column(_db(session)) == ['p_3']
    alters = [s for s in session.executed if s.startswith('ALTER TABLE')]
    assert len(alters) == 1 and 'p_3.configuration' in alters[0]
