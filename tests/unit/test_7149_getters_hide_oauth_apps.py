"""#7149 review: shared configuration getters never return admin-managed OAuth apps."""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]


def _queries(path):
    source = (ROOT / path).read_text()
    return re.findall(r"session\.query\(Configuration\)\.filter\((.*?)\)\.filter_by", source, re.S)


def test_every_shared_getter_query_excludes_admin_managed_types():
    queries = _queries('utils_getters.py')
    assert len(queries) == 2
    assert all('Configuration.type.notin_(ADMIN_MANAGED_TYPES)' in query for query in queries)
