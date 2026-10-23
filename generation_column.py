"""Idempotent guard for the ``configuration.generation`` column (R-2.0.8, #6943, C-1009).

``Configuration`` maps ``generation``; a project schema created before this release lacks the
column and every ORM query on it would fail until ``add_configuration_generation_column`` ran.
The plugin therefore adds the column at init, so a deploy never leaves ``pylon_main`` unhealthy.
The admin task remains the operator-facing way to report (dry run) and re-run the migration.
"""
import re

from pylon.core.tools import log
from sqlalchemy import text
from sqlalchemy.dialects import postgresql

_PROJECT_SCHEMA = re.compile(r'^p_\d+$')
_SCHEMAS_MISSING_COLUMN = text(
    "SELECT t.table_schema FROM information_schema.tables t "
    "WHERE t.table_name = 'configuration' AND t.table_schema LIKE 'p\\_%' AND NOT EXISTS ("
    "SELECT 1 FROM information_schema.columns c "
    "WHERE c.table_schema = t.table_schema AND c.table_name = 'configuration' "
    "AND c.column_name = 'generation')"
)


def add_generation_column(session, schema: str) -> None:
    """Add the column to one project schema. Safe to repeat."""
    quoted = postgresql.dialect().identifier_preparer.quote_schema(schema)
    session.execute(text(
        f"ALTER TABLE {quoted}.configuration "
        "ADD COLUMN IF NOT EXISTS generation INTEGER NOT NULL DEFAULT 1"
    ))


def ensure_generation_column(db) -> list:
    """Add the column to every project schema that lacks it. Returns the schemas changed."""
    added = []
    with db.get_session() as session:
        for row in session.execute(_SCHEMAS_MISSING_COLUMN):
            schema = row[0]
            if not _PROJECT_SCHEMA.match(schema):
                log.warning('configuration.generation guard: skipping unexpected schema %r', schema)
                continue
            add_generation_column(session, schema)
            added.append(schema)
        if added:
            session.commit()
    if added:
        log.info('configuration.generation guard: added the column to %s', added)
    return added
