"""Governed branch of the check-connection routes (WP-1D slice 1, #6946, C-1009).

Both routes share ``governed_probe.governed_check``. Governed + a saved credential's ``configuration_uuid`` goes to
elitea_core's in-process ``governed_check_connection`` (mocked here) and the body is ignored; governed + a template or
nested credential selector is a 403 before any unsecret or expansion; a literal body keeps the legacy path; governance
off (or a type outside the ``credentials`` section) answers exactly as before.
"""
import copy
import functools
import importlib.util
import pathlib
import sys
import types

import pydantic.root_model  # noqa: F401  loaded up front: the module isolation below would drop it otherwise
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
PACKAGE = "configurations_6946_probe"

CRED = "cred_type"        # section "credentials"
PLAIN = "plain_type"      # section "llm"
UUID = "0b9f4a9e-5a43-4c34-8c1e-2f1a5b0a9d11"
SECRET_REF = "{{secret.stored_key}}"
RESOLVED = "real-secret-value"
TYPED = "typed-token-value"
REFERENCE_MESSAGE = "This credential is registered for governed use; save it and test the saved credential."
SENTINEL = "echo-this-must-not-leak"


class Harness:
    def __init__(self):
        self.governed = True
        self.has_method = True
        self.core_result = {"success": True}
        self.core_raises = None
        self.core_calls = []
        self.unsecret_calls = []
        self.expand_calls = []
        self.check_calls = []
        self.check_result = None
        self.check_raises = None
        self.json = None
        self.args = {}
        self.openapi = {}
        self.descriptors = {}

    # --- stand-ins ---
    def core_method(self, *args, **kwargs):
        self.core_calls.append((args, kwargs))
        if self.core_raises is not None:
            raise self.core_raises
        return copy.deepcopy(self.core_result)

    def check_api(self, descriptor):
        def decorator(func):
            self.descriptors[func.__module__] = descriptor
            return func
        return decorator


def _module(name, **attrs):
    module = types.ModuleType(name)
    module.__dict__.update(attrs)
    sys.modules[name] = module
    return module


def _load(harness, filename):
    package = _module(PACKAGE)
    package.__path__ = [str(ROOT)]
    for sub in ("api", "api.v2", "models", "models.pd"):
        _module(f"{PACKAGE}.{sub}").__path__ = []

    class VaultClient:
        def __init__(self, project_id):
            self.project_id = project_id

        def unsecret(self, data):
            harness.unsecret_calls.append((self.project_id, copy.deepcopy(data)))
            return {k: (RESOLVED if v == SECRET_REF else v) for k, v in data.items()}

    def register_openapi(**kwargs):
        def decorator(func):
            harness.openapi[func.__module__] = kwargs
            return func
        return decorator

    _module(
        f"{PACKAGE}.local_tools",
        APIBase=type("APIBase", (), {}),
        register_openapi=register_openapi,
        VaultClient=VaultClient,
        config=types.SimpleNamespace(ADMINISTRATION_MODE="administration", DEFAULT_MODE="default"),
    )

    class Entry:
        def __init__(self, section):
            self.section = section

        def check_connection(self, data):
            harness.check_calls.append(copy.deepcopy(data))
            if harness.check_raises is not None:
                raise harness.check_raises
            return copy.deepcopy(harness.check_result)

    _module(f"{PACKAGE}.models.pd.registry", CONFIG_TYPE_REGISTRY={CRED: Entry("credentials"), PLAIN: Entry("llm")})

    def expand_configuration(**kwargs):
        harness.expand_calls.append(kwargs)

    _module(f"{PACKAGE}.utils", expand_configuration=expand_configuration)
    request = type("Request", (), {"json": property(lambda _: harness.json),
                                   "args": property(lambda _: harness.args)})()
    _module("flask", request=request)

    module_ns = types.SimpleNamespace()
    if harness.has_method:
        module_ns.governed_check_connection = harness.core_method
    module_ns.governance_profile = types.SimpleNamespace(enabled=harness.governed)
    core = types.SimpleNamespace(module=module_ns)
    _module(
        "tools",
        auth=types.SimpleNamespace(
            decorators=types.SimpleNamespace(check_api=harness.check_api),
            current_user=lambda: {"id": 42},
        ),
        this=types.SimpleNamespace(for_module=lambda name: core if name == "elitea_core" else None),
    )

    module_name = f"{PACKAGE}.api.v2.{filename}"
    spec = importlib.util.spec_from_file_location(module_name, ROOT / "api" / "v2" / f"{filename}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def isolated_modules():
    saved = sys.modules.copy()
    try:
        yield
    finally:
        for key in set(sys.modules) - set(saved):
            sys.modules.pop(key, None)
        sys.modules.update(saved)


@pytest.fixture
def harness(isolated_modules):
    return Harness()


def single(harness, body, config_type=CRED, args=None):
    module = _load(harness, "check_connection")
    harness.json = body
    harness.args = args or {}
    return module.API().post(project_id=1, config_type=config_type)


def batch(harness, items):
    module = _load(harness, "check_connections")
    harness.json = items
    return module.API().post(project_id=1)


def template_body():
    return {"token": SECRET_REF, "url": "http://example.invalid/"}


def literal_body():
    return {"token": TYPED, "url": "http://example.invalid/"}


def assert_nothing_resolved(harness):
    assert harness.unsecret_calls == [], "vault must not be read"
    assert harness.expand_calls == [], "no configuration may be expanded"
    assert harness.check_calls == [], "no outbound check may run"


# --- governed + saved credential ---------------------------------------------------------------------------------

def test_governed_uuid_calls_core_and_ignores_body(harness):
    response, status = single(harness, template_body(), args={"configuration_uuid": UUID})

    assert (response, status) == ({"success": True}, 200)
    assert harness.core_calls == [((1, UUID, CRED, 42), {})]
    assert_nothing_resolved(harness)


def test_governed_uuid_is_normalised_before_the_call(harness):
    single(harness, {}, args={"configuration_uuid": UUID.upper()})

    assert harness.core_calls[0][0][1] == UUID


def test_governed_uuid_success_passes_extra_fields_through(harness):
    harness.core_result = {"success": True, "tools": ["a", "b"], "warning": "w"}

    assert single(harness, {}, args={"configuration_uuid": UUID}) == (
        {"success": True, "tools": ["a", "b"], "warning": "w"}, 200)


@pytest.mark.parametrize("code", [
    "DENIED_RBAC", "CREDENTIAL_REFERENCE_INACTIVE", "CREDENTIAL_GENERATION_MISMATCH", "WORKER_CAPABILITY_UNAVAILABLE",
    "STORE_UNAVAILABLE", "DEPENDENCY_UNAVAILABLE",
])
def test_governed_refusal_is_403_with_code_and_message(harness, code):
    harness.core_result = {"success": False, "code": code, "message": "Fixed text.", "detail": SENTINEL}

    response, status = single(harness, {}, args={"configuration_uuid": UUID})

    assert status == 403
    assert response == {"success": False, "code": code, "message": "Fixed text."}  # nothing else is passed along
    assert_nothing_resolved(harness)


def test_governed_provider_failure_is_400_with_the_scrubbed_text(harness):
    harness.core_result = {"success": False, "message": "401 Unauthorized"}

    assert single(harness, {}, args={"configuration_uuid": UUID}) == (
        {"success": False, "message": "401 Unauthorized"}, 400)


def test_governed_requires_authorization_keeps_the_legacy_401_shape(harness):
    harness.core_result = {"success": False, "requires_authorization": True, "auth_metadata": {"url": "u"},
                           "message": "Authorization required for x"}

    assert single(harness, {}, args={"configuration_uuid": UUID}) == (
        {"success": False, "requires_authorization": True, "auth_metadata": {"url": "u"},
         "message": "Authorization required for x"}, 401)


@pytest.mark.parametrize("reply", [None, "ok", ["success"], {}, {"success": False}, {"success": "yes"}])
def test_unusable_reply_is_a_refusal_never_a_pass(harness, reply):
    harness.core_result = reply

    response, status = single(harness, {}, args={"configuration_uuid": UUID})

    assert status == 403 and response["code"] == "DEPENDENCY_UNAVAILABLE" and response["success"] is False
    assert_nothing_resolved(harness)


def test_core_failure_is_a_refusal_and_its_text_is_not_returned_or_logged(harness, monkeypatch):
    logged = []
    monkeypatch.setattr(sys.modules["pylon.core.tools"].log, "error", lambda *a, **k: logged.append((a, k)),
                        raising=False)
    harness.core_raises = RuntimeError(SENTINEL)

    response, status = single(harness, template_body(), args={"configuration_uuid": UUID})

    assert status == 403 and response["code"] == "DEPENDENCY_UNAVAILABLE"
    assert SENTINEL not in repr(response) and SENTINEL not in repr(logged)
    assert_nothing_resolved(harness)


def test_missing_core_method_is_a_refusal(harness):
    harness.has_method = False

    response, status = single(harness, template_body(), args={"configuration_uuid": UUID})

    assert status == 403 and response["code"] == "DEPENDENCY_UNAVAILABLE"
    assert_nothing_resolved(harness)


def test_invalid_uuid_is_400_and_not_echoed(harness):
    response, status = single(harness, template_body(), args={"configuration_uuid": SENTINEL})

    assert (response, status) == ({"success": False, "message": "Invalid configuration_uuid"}, 400)
    assert harness.core_calls == []
    assert_nothing_resolved(harness)


# --- governed + body -----------------------------------------------------------------------------------------------

BODIES = {
    "template": {"token": SECRET_REF},
    "template_in_text": {"token": "Bearer {{secret.stored_key}}"},
    "nested_template": {"auth": {"deep": [{"token": SECRET_REF}]}},
    "selector_title": {"ai_credentials": {"elitea_title": "azure_creds", "private": False}},
    "selector_uuid": {"nested": {"configuration_uuid": UUID}},
    "selector_in_list": {"items": [{"alita_title": "old_style"}]},
    "top_level_selector": {"elitea_title": "azure_creds"},
}


@pytest.mark.parametrize("body", list(BODIES.values()), ids=list(BODIES))
def test_governed_template_or_selector_body_is_403_before_any_resolution(harness, body):
    response, status = single(harness, copy.deepcopy(body))

    assert status == 403
    assert response == {"success": False, "code": "CREDENTIAL_REFERENCE_REQUIRED", "message": REFERENCE_MESSAGE}
    assert harness.core_calls == []
    assert_nothing_resolved(harness)


def test_governed_body_beyond_the_walk_bounds_counts_as_a_reference(harness):
    deep = current = {}
    for _ in range(40):
        current["x"] = {}
        current = current["x"]
    wide = {"k%d" % i: "v" for i in range(6000)}

    for body in (deep, wide):
        response, status = single(harness, body)
        assert status == 403 and response["code"] == "CREDENTIAL_REFERENCE_REQUIRED"
    assert_nothing_resolved(harness)


def test_governed_literal_body_keeps_the_legacy_path(harness):
    response, status = single(harness, literal_body())

    assert (response, status) == ({"success": True}, 200)
    assert harness.core_calls == []
    assert harness.unsecret_calls == [(1, literal_body())]
    assert harness.expand_calls and harness.expand_calls[0]["unsecret"] is True
    assert harness.check_calls == [literal_body()]


def test_governed_non_credential_type_is_legacy_even_with_uuid_and_template(harness):
    response, status = single(harness, template_body(), config_type=PLAIN, args={"configuration_uuid": UUID})

    assert (response, status) == ({"success": True}, 200)
    assert harness.core_calls == []
    assert harness.check_calls == [{"token": RESOLVED, "url": "http://example.invalid/"}]


# --- legacy responses stay byte-equal: governance off, and literal bodies when governed ----------------------------

LEGACY_CASES = {
    "success": (None, None, ({"success": True}, 200)),
    "success_with_tools": ({"tools": ["t"]}, None, ({"success": True, "tools": ["t"]}, 200)),
    "provider_text": ("bad credentials", None, ({"success": False, "message": "bad credentials"}, 400)),
    "authorization": (
        {"requires_authorization": True, "auth_metadata": {"a": 1}, "error": "login"}, None,
        ({"success": False, "requires_authorization": True, "auth_metadata": {"a": 1}, "message": "login"}, 401)),
    "value_error": (None, ValueError("bad value"), ({"success": False, "message": "bad value"}, 400)),
    "other_error": (None, RuntimeError(SENTINEL), ({"success": False, "message": "Failed to check connection"}, 400)),
}


@pytest.mark.parametrize("governed", [False, True], ids=["flag_off", "flag_on_literal"])
@pytest.mark.parametrize("case", list(LEGACY_CASES))
def test_single_legacy_responses_are_unchanged(harness, governed, case):
    harness.governed = governed
    harness.check_result, harness.check_raises, expected = LEGACY_CASES[case]
    body = literal_body() if governed else template_body()

    assert single(harness, body, args={"configuration_uuid": UUID} if not governed else {}) == expected
    assert harness.core_calls == []


def test_flag_off_template_body_with_uuid_resolves_as_before(harness):
    harness.governed = False

    response, status = single(harness, template_body(), args={"configuration_uuid": UUID})

    assert (response, status) == ({"success": True}, 200)
    assert harness.core_calls == []
    assert harness.unsecret_calls == [(1, template_body())]
    assert harness.check_calls == [{"token": RESOLVED, "url": "http://example.invalid/"}]


def test_flag_off_selector_body_is_expanded_as_before(harness):
    harness.governed = False
    body = {"ai_credentials": {"elitea_title": "azure_creds", "private": False}}

    assert single(harness, body) == ({"success": True}, 200)
    assert len(harness.expand_calls) == 1 and harness.core_calls == []


def test_governance_profile_absent_on_an_older_core_means_legacy(harness):
    harness.governed = None  # replaced below: no attribute at all

    module = _load(harness, "check_connection")
    sys.modules["tools"].this.for_module("elitea_core").module.__dict__.pop("governance_profile")
    harness.json = template_body()

    assert module.API().post(project_id=1, config_type=CRED) == ({"success": True}, 200)
    assert harness.core_calls == []


# --- batch ---------------------------------------------------------------------------------------------------------

def test_batch_governed_per_item(harness):
    harness.core_result = {"success": False, "code": "DENIED_RBAC", "message": "Not allowed."}
    items = [
        {"id": "a", "type": CRED, "data": template_body(), "configuration_uuid": UUID},   # saved credential
        {"id": "b", "type": CRED, "data": template_body()},                               # template, no uuid
        {"id": "c", "type": CRED, "data": literal_body()},                                # literal: legacy
        {"id": "d", "type": "nope", "data": {}},                                          # unchanged
        {"id": "e", "type": PLAIN, "data": template_body()},                              # not a credential: legacy
    ]

    results, status = batch(harness, items)

    assert status == 200
    assert results == [
        {"id": "a", "success": False, "code": "DENIED_RBAC", "message": "Not allowed."},
        {"id": "b", "success": False, "code": "CREDENTIAL_REFERENCE_REQUIRED", "message": REFERENCE_MESSAGE},
        {"id": "c", "success": True},
        {"id": "d", "success": False, "unsupported": True},
        {"id": "e", "success": True},
    ]
    assert harness.core_calls == [((1, UUID, CRED, 42), {})]
    assert harness.check_calls == [literal_body(), {"token": RESOLVED, "url": "http://example.invalid/"}]


def test_batch_governed_success_and_provider_failure_items(harness):
    harness.core_result = {"success": True}
    ok, _ = batch(harness, [{"id": "a", "type": CRED, "configuration_uuid": UUID}])
    harness.core_result = {"success": False, "message": "401 Unauthorized"}
    failed, _ = batch(harness, [{"id": "a", "type": CRED, "configuration_uuid": UUID}])
    invalid, _ = batch(harness, [{"id": "a", "type": CRED, "configuration_uuid": "not-a-uuid"}])

    assert ok == [{"id": "a", "success": True}]
    assert failed == [{"id": "a", "success": False, "message": "401 Unauthorized"}]
    assert invalid == [{"id": "a", "success": False, "message": "Invalid configuration_uuid"}]


def test_batch_flag_off_is_byte_equal_to_legacy(harness):
    harness.governed = False
    harness.check_result = "bad credentials"
    items = [
        {"id": "a", "type": CRED, "data": template_body(), "configuration_uuid": UUID},
        {"id": "b", "type": CRED, "data": {"ai": {"elitea_title": "t"}}},
        {"id": "c", "type": "nope", "data": {}},
    ]

    results, status = batch(harness, items)

    assert status == 200
    assert results == [
        {"id": "a", "success": False, "message": "bad credentials"},
        {"id": "b", "success": False, "message": "bad credentials"},
        {"id": "c", "success": False, "unsupported": True},
    ]
    assert harness.core_calls == []
    assert len(harness.unsecret_calls) == 2 and len(harness.expand_calls) == 2


def test_batch_reads_the_profile_once_per_request(harness):
    module = _load(harness, "check_connections")
    reads = []
    core = sys.modules["tools"].this.for_module("elitea_core")

    class Profile:
        @property
        def enabled(self):
            reads.append(1)
            return True

    core.module.governance_profile = Profile()
    harness.json = [{"id": str(i), "type": CRED, "data": literal_body()} for i in range(5)]

    module.API().post(project_id=1)

    assert len(reads) == 1


# --- declarations ---------------------------------------------------------------------------------------------------

def test_single_route_documents_the_query_parameter(harness):
    _load(harness, "check_connection")

    parameters = harness.openapi[f"{PACKAGE}.api.v2.check_connection"]["parameters"]
    query = [p for p in parameters if p["name"] == "configuration_uuid"]
    assert len(query) == 1
    assert query[0]["in"] == "query" and query[0]["required"] is False and query[0]["schema"]["format"] == "uuid"
    assert [p["name"] for p in parameters[:2]] == ["project_id", "config_type"]


def test_batch_item_model_declares_the_optional_uuid(harness):
    module = _load(harness, "check_connections")

    field = module.BatchCheckItem.model_fields["configuration_uuid"]
    assert field.is_required() is False and field.default is None


@pytest.mark.parametrize("filename", ["check_connection", "check_connections"])
def test_unsecret_permission_is_still_required(harness, filename):
    _load(harness, filename)

    assert harness.descriptors[f"{PACKAGE}.api.v2.{filename}"]["permissions"] == [
        "configuration.secrets.secret.unsecret"]
