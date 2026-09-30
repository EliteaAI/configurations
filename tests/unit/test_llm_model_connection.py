"""LLM model connection tests validate the unsaved form exactly as Save does before any request leaves.

Guards: DIAL + azure (explicit or unset) + reasoning is rejected without reaching the gateway, the RPC gets
its timeout budget, success reads as success on the single and batch endpoints, failure becomes a message, and
no raw exception text, credential key or credential host reaches the response.
"""
import importlib.util
import pathlib
import sys
import types

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
PACKAGE = "configurations_llm_connection"

SETTINGS_BASE = {
    "name": "gpt-4o",
    "supports_reasoning": False,
    "ai_credentials": {
        "elitea_title": "creds",
        "private": False,
        "api_base": "https://dial.internal.example",
        "api_key": "live-secret-key-123",
        "configuration_uuid": "cred-uuid",
        "configuration_project_id": 7,
        "configuration_type": "open_ai",
    },
}


class FakeConnectionRpc:

    def __init__(self, result=None, raises=None):
        self.result = result if result is not None else {"latency_ms": 812}
        self.raises = raises
        self.timeouts = []
        self.calls = []

    def timeout(self, seconds):
        self.timeouts.append(seconds)
        return _TimedConnectionRpc(self)


class _TimedConnectionRpc:

    def __init__(self, rpc):
        self._rpc = rpc

    def litellm_test_llm_model_connection(self, settings):
        self._rpc.calls.append(settings)
        if self._rpc.raises:
            raise self._rpc.raises
        return self._rpc.result


def _settings(credential_type="open_ai", **overrides):
    credentials = {**SETTINGS_BASE["ai_credentials"], "configuration_type": credential_type}
    return {**SETTINGS_BASE, "ai_credentials": credentials, **overrides}


def _install_package(monkeypatch, rpc):
    package = types.ModuleType(PACKAGE)
    package.__path__ = [str(ROOT)]
    models = types.ModuleType(f"{PACKAGE}.models")
    models.__path__ = [str(ROOT / "models")]
    pd_package = types.ModuleType(f"{PACKAGE}.models.pd")
    pd_package.__path__ = [str(ROOT / "models" / "pd")]
    local_tools = types.ModuleType(f"{PACKAGE}.local_tools")
    local_tools.log = types.SimpleNamespace(
        info=lambda *_a, **_k: None, error=lambda *_a, **_k: None, exception=lambda *_a, **_k: None,
    )
    local_tools.rpc_manager = rpc
    for name, module in (
        (PACKAGE, package), (f"{PACKAGE}.models", models), (f"{PACKAGE}.models.pd", pd_package),
        (f"{PACKAGE}.local_tools", local_tools),
    ):
        monkeypatch.setitem(sys.modules, name, module)
    for name in [name for name in sys.modules if name.startswith(f"{PACKAGE}.models.pd.")]:
        monkeypatch.delitem(sys.modules, name)
    monkeypatch.delitem(sys.modules, f"{PACKAGE}.exceptions", raising=False)


def _load(monkeypatch, module, rpc):
    _install_package(monkeypatch, rpc)
    spec = importlib.util.spec_from_file_location(
        f"{PACKAGE}.models.pd.{module}", ROOT / "models" / "pd" / f"{module}.py",
    )
    loaded = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, loaded)
    spec.loader.exec_module(loaded)
    return loaded


@pytest.fixture
def rpc():
    return FakeConnectionRpc()


@pytest.fixture
def connection(monkeypatch, rpc):
    return _load(monkeypatch, "llm_model_connection", rpc)


@pytest.mark.parametrize("api_protocol", ["azure", None, ""])
def test_dial_azure_or_unset_protocol_with_reasoning_is_rejected_before_the_gateway(connection, rpc, api_protocol):
    result = connection.check_llm_model_connection(
        _settings("ai_dial", supports_reasoning=True, api_protocol=api_protocol),
    )
    assert result["success"] is False
    assert "api_protocol='azure' does not support reasoning" in result["message"]
    assert rpc.calls == []


@pytest.mark.parametrize("api_protocol", ["openai", "anthropic"])
def test_dial_reasoning_on_a_supporting_protocol_reaches_the_gateway(connection, rpc, api_protocol):
    connection.check_llm_model_connection(_settings("ai_dial", supports_reasoning=True, api_protocol=api_protocol))
    assert len(rpc.calls) == 1


def test_non_dial_reasoning_needs_no_protocol(connection, rpc):
    connection.check_llm_model_connection(_settings("azure_open_ai", supports_reasoning=True))
    assert len(rpc.calls) == 1


def test_the_unsaved_form_values_are_sent_with_the_timeout_budget(connection, rpc):
    settings = _settings(name="typed-but-unsaved")
    connection.check_llm_model_connection(settings)
    assert rpc.calls == [settings]
    assert rpc.timeouts == [connection.TEST_RPC_TIMEOUT_SECONDS]


def test_success_is_reported_the_way_every_check_connection_endpoint_reads_success(connection):
    assert connection.check_llm_model_connection(_settings()) is None


def test_other_invalid_values_fail_with_the_save_message(connection, rpc):
    result = connection.check_llm_model_connection(_settings(description="x" * 41))
    assert result["success"] is False
    assert "40 characters" in result["message"]
    assert rpc.calls == []


def test_an_unexpanded_credential_fails_without_the_gateway(connection, rpc):
    result = connection.check_llm_model_connection({**SETTINGS_BASE, "ai_credentials": {"elitea_title": "creds"}})
    assert result == {"success": False, "message": connection.UNRESOLVED_CREDENTIALS_MESSAGE}
    assert rpc.calls == []


def test_a_gateway_failure_message_loses_the_credential_key_and_host(monkeypatch):
    rpc = FakeConnectionRpc(result={
        "success": False,
        "message": "Authentication failed: key live-secret-key-123 rejected by dial.internal.example",
    })
    connection = _load(monkeypatch, "llm_model_connection", rpc)
    result = connection.check_llm_model_connection(_settings())
    assert result["success"] is False
    assert result["message"].startswith("Authentication failed: key [redacted] rejected by [redacted]")


@pytest.mark.parametrize("error, message_attr", [
    (TimeoutError("rpc timed out"), "TIMED_OUT_MESSAGE"),
    (RuntimeError("Traceback ... http://10.0.0.5/internal live-secret-key-123"), "INCOMPLETE_TEST_MESSAGE"),
])
def test_an_rpc_exception_never_leaks_its_text(monkeypatch, error, message_attr):
    connection = _load(monkeypatch, "llm_model_connection", FakeConnectionRpc(raises=error))
    result = connection.check_llm_model_connection(_settings())
    assert result == {"success": False, "message": getattr(connection, message_attr)}


def test_an_unexpected_rpc_result_is_an_incomplete_test(monkeypatch):
    connection = _load(monkeypatch, "llm_model_connection", FakeConnectionRpc(result="done"))
    assert connection.check_llm_model_connection(_settings())["message"] == connection.INCOMPLETE_TEST_MESSAGE


def test_the_registry_reports_success_as_none_and_failure_as_a_message(monkeypatch):
    rpc = FakeConnectionRpc()
    registry = _load(monkeypatch, "registry", rpc)
    llm_model = registry.CONFIG_TYPE_REGISTRY["llm_model"]
    assert hasattr(llm_model.model, "check_connection")
    assert llm_model.check_connection(_settings()) is None

    rpc.result = {"success": False, "message": "Model not found: no such deployment"}
    assert llm_model.check_connection(_settings()) == "Model not found: no such deployment"


def test_the_registry_never_marks_other_model_types_testable(monkeypatch):
    registry = _load(monkeypatch, "registry", FakeConnectionRpc())
    for type_name in ("embedding_model", "image_generation_model", "asr_model"):
        assert not hasattr(registry.CONFIG_TYPE_REGISTRY[type_name].model, "check_connection")
