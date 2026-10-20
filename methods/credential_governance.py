"""In-process reads that credential onboarding needs from configurations (#6757, C-1009).

``configuration_credential_facts`` is metadata only. ``configuration_secret_bundle`` returns secret
material, so it answers only a store context minted by the credential onboarding service; the material
is returned as ``SecretStr`` and never logged. Both are plain module methods (same process), not RPC:
material must not cross the Redis bus.
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
MAX_SCHEMA_DEPTH = 4
BUNDLE_PURPOSES = frozenset({"onboard", "probe"})


def collect_secret_fields(properties, data, resolve, prefix="", depth=0) -> dict:
    """``{field path: SecretStr}`` for every password field of ``data`` that has a value, nested objects joined
    with ``.``. ``resolve`` turns a stored ``{{secret.*}}`` reference into the value; a field that stays a
    reference is an error (``ValueError("SECRET_UNRESOLVED")``): never partial material."""
    fields = {}
    for name, schema in (properties or {}).items():
        if not isinstance(schema, dict) or name not in data:
            continue
        value, path = data[name], f"{prefix}{name}"
        if _is_password_schema(schema):
            if value in (None, ""):
                continue
            plain = resolve(value) if isinstance(value, str) else None
            if not isinstance(plain, str) or (plain.startswith("{{secret.") and plain.endswith("}}")):
                raise ValueError("SECRET_UNRESOLVED")
            if plain:
                fields[path] = SecretStr(plain)
        elif isinstance(value, dict) and depth < MAX_SCHEMA_DEPTH and isinstance(schema.get("properties"), dict):
            fields.update(collect_secret_fields(schema["properties"], value, resolve, f"{path}.", depth + 1))
    return fields


class Method:
    @web.method()
    def configuration_credential_facts(self, project_id: int, uuids: list) -> dict:
        """What a credential reference needs to know about configurations, by uuid. No data, no secrets.

        ``{uuid: {id, project_id, type, section, generation}}`` for the uuids that exist in the project's schema.
        """
        if not isinstance(uuids, (list, tuple)) or len(uuids) > MAX_FACT_UUIDS:
            raise ValueError("at most %d configuration uuids per call" % MAX_FACT_UUIDS)
        wanted = [uuid.UUID(str(item)) for item in uuids]
        if not wanted:
            return {}
        with db.get_session(project_id) as session:
            rows = session.query(
                Configuration.uuid, Configuration.id, Configuration.project_id, Configuration.type,
                Configuration.section, Configuration.generation,
            ).filter(Configuration.uuid.in_(wanted)).all()
        return {
            str(row.uuid): {"id": row.id, "project_id": row.project_id, "type": row.type,
                            "section": row.section, "generation": row.generation}
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
