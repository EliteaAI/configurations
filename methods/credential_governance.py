"""In-process reads that credential onboarding needs from configurations (#6757, C-1009).

``configuration_credential_facts`` is metadata only. ``configuration_secret_bundle`` returns secret
material, so it answers only a store context minted by the credential onboarding service; the material
is returned as ``SecretStr`` and never logged. ``configuration_secret_fields`` (#6946) names the secret field
paths of a type from its schema alone, with the walker the bundle uses. All are plain module methods (same
process), not RPC: material must not cross the Redis bus.
"""
import uuid
from copy import deepcopy

from pydantic import SecretStr
from pylon.core.tools import web

from ..local_tools import VaultClient, db
from ..models.configuration import Configuration
from ..models.pd.registry import CONFIG_TYPE_REGISTRY
from ..utils import _is_password_schema

MAX_FACT_UUIDS = 64
MAX_FIELD_TYPES = 256
MAX_SCHEMA_DEPTH = 4
BUNDLE_PURPOSES = frozenset({"onboard", "probe"})


def _is_secret_collection(schema) -> bool:
    """A schema (or an ``anyOf`` option of it) whose values are password strings keyed by name or position:
    a map (``additionalProperties`` is a password schema, e.g. the SDK's ``Dict[str, SecretStr]``) or an array
    (``items`` is). ``_process_secret_value`` seals the map at runtime; an array is not sealed there, so a
    stored one is imported and sealed here rather than left as clear text."""
    for option in (schema, *schema.get("anyOf", ())):
        if isinstance(option, dict) and any(
                isinstance(option.get(key), dict) and _is_password_schema(option[key])
                for key in ("additionalProperties", "items")):
            return True
    return False


def _resolved_secret(value, resolve):
    """The plain text of a stored password value, ``None`` when it is empty. A value that stays a reference (or is
    not text) is ``ValueError("SECRET_UNRESOLVED")``."""
    if value in (None, ""):
        return None
    plain = resolve(value) if isinstance(value, str) else None
    if not isinstance(plain, str) or (plain.startswith("{{secret.") and plain.endswith("}}")):
        raise ValueError("SECRET_UNRESOLVED")
    return plain or None


def collect_secret_fields(properties, data, resolve, prefix="", depth=0) -> dict:
    """``{field path: SecretStr}`` for every password field of ``data`` that has a value, nested objects joined
    with ``.``. A map (or array) of passwords contributes one ``<name>.<key>`` (``<name>.<index>``) per entry
    present. ``resolve`` turns a stored ``{{secret.*}}`` reference into the value; a field that stays a
    reference is an error (``ValueError("SECRET_UNRESOLVED")``): never partial material."""
    fields = {}
    for name, schema in (properties or {}).items():
        if not isinstance(schema, dict) or name not in data:
            continue
        value, path = data[name], f"{prefix}{name}"
        if _is_password_schema(schema):
            plain = _resolved_secret(value, resolve)
            if plain:
                fields[path] = SecretStr(plain)
        elif _is_secret_collection(schema):
            entries = value.items() if isinstance(value, dict) else enumerate(value) if isinstance(value, list) else ()
            for key, item in entries:
                plain = _resolved_secret(item, resolve)
                if plain:
                    fields[f"{path}.{key}"] = SecretStr(plain)
        elif isinstance(value, dict) and depth < MAX_SCHEMA_DEPTH and isinstance(schema.get("properties"), dict):
            fields.update(collect_secret_fields(schema["properties"], value, resolve, f"{path}.", depth + 1))
    return fields


def _placeholder_data(properties, depth=0) -> dict:
    """A value for every property of a schema (nested objects as dicts), so ``collect_secret_fields`` can walk
    the schema without a stored configuration. The placeholder is a constant, not a secret. A map or array of
    passwords gets the single entry ``*``, which is how its paths come out as ``<name>.*``."""
    data = {}
    for name, schema in (properties or {}).items():
        if not isinstance(schema, dict):
            continue
        nested = schema.get("properties")
        if not _is_password_schema(schema) and _is_secret_collection(schema):
            data[name] = {"*": "x"}
        elif not _is_password_schema(schema) and isinstance(nested, dict) and depth < MAX_SCHEMA_DEPTH:
            data[name] = _placeholder_data(nested, depth + 1)
        else:
            data[name] = "x"
    return data


def secret_field_paths(properties) -> list:
    """The paths ``collect_secret_fields`` would return for a configuration that has every field set; a map or
    array of passwords is the wildcard ``<name>.*``."""
    return list(collect_secret_fields(properties, _placeholder_data(properties), lambda value: value))


class Method:
    @web.method()
    def configuration_credential_facts(self, project_id: int, uuids: list) -> dict:
        """What a credential reference needs to know about configurations, by uuid. No data, no secrets.

        ``{uuid: {id, project_id, type, section, generation, title}}`` for the uuids that exist in the project's schema.
        """
        if not isinstance(uuids, (list, tuple)) or len(uuids) > MAX_FACT_UUIDS:
            raise ValueError("at most %d configuration uuids per call" % MAX_FACT_UUIDS)
        wanted = [uuid.UUID(str(item)) for item in uuids]
        if not wanted:
            return {}
        with db.get_session(project_id) as session:
            rows = session.query(
                Configuration.uuid, Configuration.id, Configuration.project_id, Configuration.type,
                Configuration.section, Configuration.generation, Configuration.elitea_title,
            ).filter(Configuration.uuid.in_(wanted)).all()
        return {
            str(row.uuid): {"id": row.id, "project_id": row.project_id, "type": row.type,
                            "section": row.section, "generation": row.generation, "title": row.elitea_title}
            for row in rows
        }

    @web.method()
    def configuration_secret_bundle(self, project_id: int, configuration_id: int, ctx) -> dict | None:
        """The secret fields of one configuration, for onboarding into a credential store.

        Needs a store context of purpose ``onboard`` or ``probe`` for this project, as minted by the credential
        onboarding service; anything else is a ``PermissionError``. Returns ``None`` when the configuration does
        not exist, else ``{"configuration_generation": g, "fields": {name: SecretStr}}``. Fixed ``ValueError``
        codes: ``NO_SECRET_FIELDS`` (nothing to import), ``SECRET_UNRESOLVED`` (a reference does not resolve).
        """
        from tools import secret_stores  # pylint: disable=C0415,E0401

        if (
                type(ctx) is not secret_stores.AuthorizedStoreContext  # pylint: disable=C0123
                or ctx.purpose not in BUNDLE_PURPOSES
                or ctx.project_id != project_id
        ):
            raise PermissionError("not authorized to read configuration secrets")
        with db.get_session(project_id) as session:
            config = session.query(Configuration).filter(
                Configuration.id == configuration_id, Configuration.project_id == project_id,
            ).first()
            if config is None:
                return None
            config_type, generation, data = config.type, config.generation, deepcopy(config.data or {})
        entry = CONFIG_TYPE_REGISTRY.get(config_type)
        schema = (entry.config_schema or {}) if entry is not None else {}
        fields = collect_secret_fields(schema.get("properties"), data, VaultClient(project_id).unsecret)
        if not fields:
            raise ValueError("NO_SECRET_FIELDS")
        return {"configuration_generation": generation, "fields": fields}

    @web.method()
    def configuration_secret_fields(self, types: list) -> dict:
        """The secret field paths of configuration types, from their registered schemas. No database, no values.

        ``{type: [path, ...]}`` for each registered type in ``types``; nested objects are joined with ``.`` and the
        paths are exactly the field names ``configuration_secret_bundle`` returns for a configuration of that type
        (same walker). A type without secret fields maps to ``[]``; an unregistered type is left out, so a caller
        that needs every type to be known can tell. At most 256 names, ``ValueError`` otherwise.

        Path grammar: ``name`` (a password field), ``a.b`` (nested object field), and the wildcard ``<path>.*``
        for a map of passwords (every key of that dict, e.g. ``headers.*`` for OpenAPI headers) or an array of
        passwords (every element). A consumer such as the elitea_core seal treats everything under ``<path>.*``
        as secret; the bundle names those entries ``<path>.<key>`` (``<path>.<index>`` for an array), one per
        entry present.
        """
        if (
                not isinstance(types, (list, tuple, set, frozenset)) or len(types) > MAX_FIELD_TYPES
                or not all(isinstance(name, str) for name in types)
        ):
            raise ValueError("at most %d configuration type names per call" % MAX_FIELD_TYPES)
        fields = {}
        for name in types:
            entry = CONFIG_TYPE_REGISTRY.get(name)
            if entry is not None:
                fields[name] = secret_field_paths((entry.config_schema or {}).get("properties"))
        return fields
