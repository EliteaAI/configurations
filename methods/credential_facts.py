"""Metadata-only list of the credential configurations of a project (#7119, C-1009).

The operator UI offers these in the "create credential reference" dialog. A row is built from explicit metadata
columns: ``Configuration.data`` is never selected, so no stored value (secret or not) can reach a response.
``has_secret_fields`` comes from the type's registered schema alone (same walker as the secret bundle), not from
the stored data.
"""
from sqlalchemy import func, or_

from pylon.core.tools import web

from ..governed_probe import CREDENTIALS_SECTION
from ..local_tools import db
from ..models.configuration import Configuration
from .credential_governance import registered_secret_paths, secret_field_paths  # pylint: disable=W0611

DEFAULT_LIST_LIMIT = 20
MAX_LIST_LIMIT = 100
MAX_SEARCH_LENGTH = 128
LIKE_ESCAPE = "\\"


def _non_negative_int(value, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(code)
    return value


def _like_pattern(search: str) -> str:
    """``%search%`` with the LIKE wildcards of the input taken literally."""
    escaped = search.replace(LIKE_ESCAPE, LIKE_ESCAPE * 2).replace("%", LIKE_ESCAPE + "%").replace("_", LIKE_ESCAPE + "_")
    return f"%{escaped}%"


def _has_secret_fields(config_type: str, cache: dict) -> bool:
    if config_type not in cache:
        cache[config_type] = bool(registered_secret_paths(config_type))
    return cache[config_type]


class Method:
    @web.method()
    def configuration_credential_list(
            self, project_id: int, section: str = CREDENTIALS_SECTION, search: str | None = None,
            limit: int = DEFAULT_LIST_LIMIT, offset: int = 0,
    ) -> dict:
        """The configurations of a project that can be registered as governed credential references. No data, no secrets.

        ``{total, rows: [{configuration_id, uuid, configuration_type, title, label, section, generation, shared,
        author_id, created_at, has_secret_fields}]}``, newest first. ``section`` must be ``credentials`` (anything
        else is ``ValueError("INVALID_SECTION")``); ``search`` is a literal substring of the title or label (at most
        128 characters, else ``INVALID_SEARCH``); ``limit`` is capped at 100 (``INVALID_LIMIT`` below 1 or not an
        integer); ``INVALID_OFFSET`` for a negative or non-integer offset.
        """
        if section != CREDENTIALS_SECTION:
            raise ValueError("INVALID_SECTION")
        limit = min(_non_negative_int(limit, "INVALID_LIMIT"), MAX_LIST_LIMIT)
        if limit < 1:
            raise ValueError("INVALID_LIMIT")
        offset = _non_negative_int(offset, "INVALID_OFFSET")
        search = "" if search is None else search
        if not isinstance(search, str) or len(search.strip()) > MAX_SEARCH_LENGTH:
            raise ValueError("INVALID_SEARCH")
        search = search.strip()

        conditions = [Configuration.project_id == project_id, Configuration.section == section]
        if search:
            pattern = _like_pattern(search)
            conditions.append(or_(
                Configuration.elitea_title.ilike(pattern, escape=LIKE_ESCAPE),
                Configuration.label.ilike(pattern, escape=LIKE_ESCAPE),
            ))
        with db.get_session(project_id) as session:
            total = session.query(func.count(Configuration.id)).filter(*conditions).scalar() or 0
            found = session.query(
                Configuration.id, Configuration.uuid, Configuration.type, Configuration.elitea_title,
                Configuration.label, Configuration.section, Configuration.generation, Configuration.shared,
                Configuration.author_id, Configuration.created_at,
            ).filter(*conditions).order_by(
                Configuration.created_at.desc(), Configuration.id.desc(),
            ).offset(offset).limit(limit).all()
        types_seen: dict = {}
        return {
            "total": total,
            "rows": [
                {
                    "configuration_id": row.id,
                    "uuid": str(row.uuid),
                    "configuration_type": row.type,
                    "title": row.elitea_title,
                    "label": row.label,
                    "section": row.section,
                    "generation": row.generation,
                    "shared": row.shared,
                    "author_id": row.author_id,
                    "created_at": row.created_at.isoformat() if row.created_at else None,
                    "has_secret_fields": _has_secret_fields(row.type, types_seen),
                }
                for row in found
            ],
        }
