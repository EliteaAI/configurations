"""Bulk project data delete keeps the credentials Elitea makes for itself (#7189).

The count and the delete share kept_by_bulk_delete, so the number shown as skipped is the number
skipped. The RPC methods are compiled from source with the DB and registry stubbed: no Pylon import.
"""
import ast
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[2]
RPC_PATH = ROOT / 'rpc/main.py'
NAMES = ('INTERNAL_CREDENTIAL_TYPES', 'kept_by_bulk_delete', 'configurations_count_section',
         'configurations_delete_section')


def _load_enums():
    spec = importlib.util.spec_from_file_location('bulk_delete_enums', ROOT / 'models/enums.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SourceTypes = _load_enums().SourceTypes

# type, title, source
ROWS = [
    ('github', 'my github', SourceTypes.user),       # deleted
    ('postman', 'pm', None),                         # deleted
    ('s3_api_credentials', 's3 key', SourceTypes.user),  # kept: S3 API key
    ('github', 'platform github', SourceTypes.system),   # kept: system-made
    ('managed_type', 'managed', SourceTypes.user),   # kept: plugin-managed
]


class FakeQuery:
    def __init__(self, rows):
        self.rows = rows

    def filter(self, *args):
        return self

    def all(self):
        return self.rows


class FakeSession:
    def __init__(self, rows):
        self.rows = rows

    def query(self, *columns):
        # The delete selects the id first, the count does not
        if len(columns) == 4:
            return FakeQuery([(index,) + row for index, row in enumerate(self.rows, start=1)])
        return FakeQuery(self.rows)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _compile(rows, delete_configuration=None, clock=None):
    tree = ast.parse(RPC_PATH.read_text())
    nodes = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in NAMES:
            node.decorator_list = []
            # Their imports resolve through this test's namespace
            node.body = [n for n in node.body if not isinstance(n, ast.ImportFrom)]
            nodes.append(node)
        elif isinstance(node, ast.Assign) and getattr(node.targets[0], 'id', None) in NAMES:
            nodes.append(node)
    namespace = {
        'SourceTypes': SourceTypes,
        'owned_entry': lambda config_type, title: config_type == 'managed_type' or None,
        'db': SimpleNamespace(get_session=lambda project_id: FakeSession(rows)),
        'Configuration': MagicMock(),
        'delete_configuration': delete_configuration or (lambda project_id, config_id: {'id': config_id}),
        'log': MagicMock(),
        'time': SimpleNamespace(monotonic=clock or (lambda: 0.0)),
    }
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(RPC_PATH), 'exec'), namespace)  # noqa: S102
    return namespace


@pytest.mark.parametrize('config_type, title, source, kept', [
    ('s3_api_credentials', 'key', SourceTypes.user, True),
    ('s3_api_credentials', 'key', SourceTypes.system, True),
    ('github', 'gh', SourceTypes.system, True),
    ('managed_type', 'managed', SourceTypes.user, True),
    ('github', 'gh', SourceTypes.user, False),
    ('postman', 'pm', None, False),
])
def test_kept_by_bulk_delete(config_type, title, source, kept):
    assert _compile([])['kept_by_bulk_delete'](config_type, title, source) is kept


def test_count_reports_the_kept_credentials_as_blocked():
    assert _compile(ROWS)['configurations_count_section'](None, 7, 'credentials') == {'count': 5, 'blocked': 3}


def test_delete_skips_the_kept_credentials_and_deletes_the_rest():
    deleted_ids = []
    namespace = _compile(ROWS, lambda project_id, config_id: deleted_ids.append(config_id) or {'id': config_id})

    result = namespace['configurations_delete_section'](None, 7, 'credentials')

    assert result == {'deleted': 2, 'failed': 0, 'skipped': 3, 'remaining': 0}
    assert deleted_ids == [1, 2]


def test_count_and_delete_agree_on_what_is_skipped():
    namespace = _compile(ROWS)
    blocked = namespace['configurations_count_section'](None, 7, 'credentials')['blocked']
    assert namespace['configurations_delete_section'](None, 7, 'credentials')['skipped'] == blocked


def test_a_failing_delete_is_counted_and_does_not_stop_the_rest():
    def delete_configuration(project_id, config_id):
        if config_id == 1:
            raise RuntimeError('boom')
        return {'id': config_id}

    result = _compile(ROWS, delete_configuration)['configurations_delete_section'](None, 7, 'credentials')

    assert result == {'deleted': 1, 'failed': 1, 'skipped': 3, 'remaining': 0}


def test_the_time_budget_stops_the_run_and_reports_what_is_left():
    ticks = iter(range(100))
    deleted_ids = []
    namespace = _compile(
        ROWS, lambda project_id, config_id: deleted_ids.append(config_id) or {'id': config_id},
        clock=lambda: float(next(ticks)),
    )

    # budget 1.5: start at 0, first row checked at 1, stop at 2
    result = namespace['configurations_delete_section'](None, 7, 'credentials', time_budget=1.5)

    assert result == {'deleted': 1, 'failed': 0, 'skipped': 0, 'remaining': len(ROWS) - 1}
    assert deleted_ids == [1]
