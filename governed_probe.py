"""Governed branch of the check-connection routes (WP-1D slice 1, #6946, C-1009).

One helper serves ``api/v2/check_connection.py`` and ``api/v2/check_connections.py``. With governance off, or for a
configuration type outside the ``credentials`` section, ``governed_check`` returns ``None`` and the route runs its
legacy code untouched. Governed, a probe of a saved credential never resolves a stored value here: it is handed to
elitea_core in process (``governed_check_connection``) and the body is ignored. A body that carries a secret template
or a nested credential selector is refused before ``VaultClient.unsecret`` / ``expand_configuration``.

Nothing here logs or returns a request value; refusal texts are fixed per code.
"""
import uuid as uuidlib
from typing import Any, Optional

from pylon.core.tools import log

CREDENTIALS_SECTION = "credentials"
SECRET_TEMPLATE = "{{secret."
SELECTOR_KEYS = ("configuration_uuid", "elitea_title", "alita_title")
MAX_BODY_DEPTH = 16
MAX_BODY_NODES = 5000

CODE_REFERENCE_REQUIRED = "CREDENTIAL_REFERENCE_REQUIRED"
CODE_DEPENDENCY_UNAVAILABLE = "DEPENDENCY_UNAVAILABLE"
MESSAGES = {
    CODE_REFERENCE_REQUIRED: "This credential is registered for governed use; save it and test the saved credential.",
    CODE_DEPENDENCY_UNAVAILABLE: "A required service is unavailable; the check was not run. Try again later.",
}
GENERIC_REFUSAL = "The credential check was refused."
INVALID_UUID = "Invalid configuration_uuid"


def is_governed() -> bool:
    """The operator profile of elitea_core (``enterprise_credential_governance``), read as ``admission`` reads it."""
    from tools import this  # pylint: disable=C0415,E0401

    profile = getattr(this.for_module("elitea_core").module, "governance_profile", None)
    return bool(profile is not None and profile.enabled)


def requires_reference(data: Any) -> bool:
    """True when a body names a stored secret (``{{secret.*}}``) or a credential selector at any depth.

    The walk is bounded; a body that exceeds the bounds counts as carrying one (fail closed)."""
    budget = [MAX_BODY_NODES]

    def walk(node, depth):
        budget[0] -= 1
        if budget[0] < 0 or depth > MAX_BODY_DEPTH:
            return True
        if isinstance(node, str):
            return SECRET_TEMPLATE in node
        if isinstance(node, dict):
            if any(node.get(key) for key in SELECTOR_KEYS):
                return True
            return any(walk(value, depth + 1) for value in node.values())
        if isinstance(node, (list, tuple)):
            return any(walk(value, depth + 1) for value in node)
        return False

    return walk(data, 0)


def refusal(code: str, message: Optional[str] = None) -> tuple[dict, int]:
    return {"success": False, "code": code, "message": message or MESSAGES.get(code, GENERIC_REFUSAL)}, 403


def _shape(result: Any) -> tuple[dict, int]:
    """Map the reply of elitea_core to the response; anything unexpected is a refusal, never a pass."""
    if not isinstance(result, dict):
        return refusal(CODE_DEPENDENCY_UNAVAILABLE)
    if result.get("success") is True:
        return {**result, "success": True}, 200
    if result.get("requires_authorization"):
        return {
            "success": False,
            "requires_authorization": True,
            "auth_metadata": result.get("auth_metadata", {}),
            "message": result.get("message", "Authorization required"),
        }, 401
    code, message = result.get("code"), result.get("message")
    if isinstance(code, str) and code:
        return refusal(code, message if isinstance(message, str) and message else None)
    if isinstance(message, str):
        return {"success": False, "message": message}, 400
    return refusal(CODE_DEPENDENCY_UNAVAILABLE)


def _probe(project_id: int, configuration_uuid: str, config_type: str, user_id: Any) -> tuple[dict, int]:
    from tools import this  # pylint: disable=C0415,E0401

    try:
        method = getattr(this.for_module("elitea_core").module, "governed_check_connection", None)
        if method is None:
            log.error("governed check_connection: elitea_core.governed_check_connection is not available")
            return refusal(CODE_DEPENDENCY_UNAVAILABLE)
        result = method(project_id, configuration_uuid, config_type, user_id)
    except Exception as exc:  # pylint: disable=W0703
        log.error("governed check_connection failed: %s", type(exc).__name__)  # type only: texts may echo inputs
        return refusal(CODE_DEPENDENCY_UNAVAILABLE)
    return _shape(result)


def governed_check(entry: Any, config_type: str, data: Any, configuration_uuid: Optional[str],
                   project_id: int, user_id: Any, governed: Optional[bool] = None) -> Optional[tuple[dict, int]]:
    """``None``: run the legacy path. Else ``(body, status)`` to answer with.

    ``governed`` lets the batch route read the profile once per request."""
    if getattr(entry, "section", None) != CREDENTIALS_SECTION:
        return None
    if governed is None:
        governed = is_governed()
    if not governed:
        return None
    if configuration_uuid:
        try:
            configuration_uuid = str(uuidlib.UUID(str(configuration_uuid)))
        except ValueError:
            return {"success": False, "message": INVALID_UUID}, 400
        return _probe(project_id, configuration_uuid, config_type, user_id)
    if requires_reference(data):
        return refusal(CODE_REFERENCE_REQUIRED)
    return None
