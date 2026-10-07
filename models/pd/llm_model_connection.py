from urllib.parse import urlparse

from pydantic import ValidationError

from .llm_model import LlmModel
from ...exceptions import handle_validation_error
from ...local_tools import log, rpc_manager

TEST_RPC_TIMEOUT_SECONDS = 35
DIAL_CREDENTIAL_TYPE = 'ai_dial'
DIAL_DEFAULT_API_PROTOCOL = 'azure'
REDACTED = '[redacted]'

UNRESOLVED_CREDENTIALS_MESSAGE = 'Connection failed: the selected AI credentials could not be loaded'
TIMED_OUT_MESSAGE = 'Timed out: the provider did not answer in time'
INCOMPLETE_TEST_MESSAGE = 'Connection failed: the test could not be completed'


def check_llm_model_connection(settings: dict) -> dict | None:
    credentials = settings.get('ai_credentials') or {}
    credential_type = credentials.get('configuration_type')
    if not credential_type or not credentials.get('configuration_uuid'):
        return connection_failure(UNRESOLVED_CREDENTIALS_MESSAGE)
    #
    try:
        validate_as_saved(settings, credential_type)
    except ValidationError as error:
        return connection_failure(handle_validation_error(error).message)
    #
    try:
        result = rpc_manager.timeout(TEST_RPC_TIMEOUT_SECONDS).litellm_test_llm_model_connection(
            settings=settings,
        )
    except Exception as error:  # pylint: disable=W0718
        log.exception('LLM model connection test did not complete')
        return connection_failure(TIMED_OUT_MESSAGE if is_timeout(error) else INCOMPLETE_TEST_MESSAGE)
    #
    if not isinstance(result, dict):
        return connection_failure(INCOMPLETE_TEST_MESSAGE)
    if result.get('success') is False:
        return connection_failure(redact_credentials(result.get('message'), credentials))
    return None


def validate_as_saved(settings: dict, credential_type: str) -> LlmModel:
    credentials = settings['ai_credentials']
    payload = {
        **settings,
        'ai_credentials': {
            'elitea_title': credentials.get('elitea_title'),
            'private': bool(credentials.get('private')),
        },
    }
    if credential_type == DIAL_CREDENTIAL_TYPE:
        payload['api_protocol'] = settings.get('api_protocol') or DIAL_DEFAULT_API_PROTOCOL
    return LlmModel.model_validate(payload)


def redact_credentials(message, credentials: dict) -> str:
    result = str(message or INCOMPLETE_TEST_MESSAGE)
    for secret in credential_secrets(credentials):
        result = result.replace(secret, REDACTED)
    return result


def credential_secrets(credentials: dict) -> list[str]:
    api_base = str(credentials.get('api_base') or '')
    # The gateway sends the key without HTTP padding (#6711), so that is what an error can echo
    secrets = [str(credentials.get('api_key') or '').strip(' \t'), api_base, urlparse(api_base).hostname or '']
    return sorted({secret for secret in secrets if len(secret) > 3}, key=len, reverse=True)


def is_timeout(error: Exception) -> bool:
    return 'timeout' in type(error).__name__.lower() or 'timed out' in str(error).lower()


def connection_failure(message: str) -> dict:
    return {'success': False, 'message': message}
