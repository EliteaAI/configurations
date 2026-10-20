"""C-1009 (#7119): ``GET /api/v2/configurations/credential_candidates/administration/<project_id>``.

The handler is loaded for real. ``check_api`` is replaced by a stand-in that grants a caller what the endpoint's own
``recommended_roles`` grant (and, for a scoped token, only what the token carries), so the matrix proves the
descriptor the endpoint declares and that nothing runs behind a refusal. The platform's enforcement itself is not
under test here.
"""
import functools
import importlib.util
import pathlib
import sys
import types

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
PACKAGE = 'configurations_7119_api_test'
PERMISSION = 'credentials.view'
SECRET_TEXT = 'fixture-secret-value'  # fake, test only
LIST_RESULT = {'total': 1, 'rows': [{'configuration_id': 3, 'uuid': 'u', 'configuration_type': 'api_cred',
                                     'title': 't', 'has_secret_fields': True}]}


class Actor:
    def __init__(self, role, kind='session', token_permissions=None):
        self.role, self.kind, self.token_permissions = role, kind, token_permissions


class Harness:
    def __init__(self):
        self.actor = None
        self.args = {}
        self.descriptor = None
        self.openapi = None
        self.rpc_calls = []
        self.rpc_result = {'id': 1, 'name': 'team'}
        self.rpc_error = None
        self.method_calls = []
        self.method_error = None

    def check_api(self, descriptor):
        self.descriptor = descriptor
        wanted = set(descriptor['permissions'])
        roles = descriptor['recommended_roles']['administration']

        def decorator(func):
            @functools.wraps(func)
            def wrapper(*args, **kwargs):
                actor = self.actor
                if actor is None:
                    return {'error': 'unauthorized'}, 401
                allowed = bool(roles.get(actor.role, False))
                if actor.kind == 'token' and actor.token_permissions is not None:
                    allowed = allowed and wanted <= actor.token_permissions
                if not allowed:
                    return {'error': 'access_denied'}, 403
                return func(*args, **kwargs)
            return wrapper
        return decorator


def _module(name, **attrs):
    module = types.ModuleType(name)
    module.__dict__.update(attrs)
    sys.modules[name] = module
    return module


def _load(harness):
    package = _module(PACKAGE)
    package.__path__ = [str(ROOT)]
    for sub in ('api', 'api.v2'):
        _module(f'{PACKAGE}.{sub}').__path__ = []

    def register_openapi(**metadata):
        def decorator(func):
            harness.openapi = metadata
            return func
        return decorator

    class APIModeHandler:
        def __init__(self, api, mode='default'):
            self._api, self.mode = api, mode

        def __getattr__(self, item):
            return getattr(self._api, item)

    class APIBase:
        def __init__(self, module):
            self.module = module

    class Rpc:
        def timeout(self, seconds):
            harness.rpc_calls.append(('timeout', seconds))
            return self

        def project_get_by_id(self, project_id):
            harness.rpc_calls.append(('project_get_by_id', project_id))
            if harness.rpc_error:
                raise harness.rpc_error
            return harness.rpc_result

    _module(f'{PACKAGE}.local_tools', register_openapi=register_openapi, rpc_manager=Rpc(),
            config=types.SimpleNamespace(ADMINISTRATION_MODE='administration', DEFAULT_MODE='default'))
    _module(f'{PACKAGE}.governed_probe', CREDENTIALS_SECTION='credentials')
    request = types.SimpleNamespace(args=harness.args)
    _module('flask', request=request)
    _module('pylon', ).__path__ = []
    _module('pylon.core').__path__ = []
    _module('pylon.core.tools', log=types.SimpleNamespace(
        warning=lambda *a, **k: None, info=lambda *a, **k: None, error=lambda *a, **k: None))
    _module('tools',
            api_tools=types.SimpleNamespace(
                APIModeHandler=APIModeHandler, APIBase=APIBase, endpoint_metrics=lambda func: func),
            auth=types.SimpleNamespace(decorators=types.SimpleNamespace(check_api=harness.check_api)))
    name = f'{PACKAGE}.api.v2.credential_candidates'
    spec = importlib.util.spec_from_file_location(name, ROOT / 'api/v2/credential_candidates.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def harness():
    saved = sys.modules.copy()
    harness = Harness()
    try:
        yield harness
    finally:
        for key in set(sys.modules) - set(saved):
            sys.modules.pop(key, None)
        sys.modules.update(saved)


def _call(harness, project_id=7, **args):
    module = _load(harness)
    harness.args.clear()
    harness.args.update(args)

    class FakeModule:
        def configuration_credential_list(self, *a, **k):
            harness.method_calls.append((a, k))
            if harness.method_error:
                raise harness.method_error
            return LIST_RESULT

    api = module.API(FakeModule())
    handler = module.API.mode_handlers['administration'](api, 'administration')
    return handler.get(project_id=project_id)


# --- declaration -------------------------------------------------------------------------------------------

def test_declares_the_view_permission_for_platform_admins_only(harness):
    harness.actor = Actor('admin')
    _call(harness)
    assert harness.descriptor['permissions'] == [PERMISSION]
    assert harness.descriptor['recommended_roles'] == {
        'administration': {'super_admin': True, 'admin': True, 'editor': False, 'viewer': False}}


def test_is_administration_mode_only_and_not_an_mcp_or_user_tool(harness):
    harness.actor = Actor('admin')
    _call(harness)
    module = sys.modules[f'{PACKAGE}.api.v2.credential_candidates']
    assert module.API.url_params == ['<string:mode>/<int:project_id>']
    assert list(module.API.mode_handlers) == ['administration']
    assert harness.openapi['mcp_tool'] is False and harness.openapi['available_to_users'] is False


# --- authorization matrix ----------------------------------------------------------------------------------

ALLOWED = [
    ('super_admin', Actor('super_admin')),
    ('admin', Actor('admin')),
    ('admin_token_with_permission', Actor('admin', 'token', {PERMISSION})),
]
DENIED = [
    ('editor', Actor('editor'), 403),
    ('viewer', Actor('viewer'), 403),
    ('project_member_without_role', Actor('none'), 403),
    ('admin_token_without_permission', Actor('admin', 'token', {'configuration.secrets.secret.unsecret'}), 403),
    ('admin_token_with_empty_scope', Actor('admin', 'token', set()), 403),
    ('editor_token_with_permission', Actor('editor', 'token', {PERMISSION}), 403),
    ('unauthenticated', None, 401),
]


@pytest.mark.parametrize('actor', [a for _, a in ALLOWED], ids=[n for n, _ in ALLOWED])
def test_allowed_actors_get_the_list(harness, actor):
    harness.actor = actor
    response, status = _call(harness)
    assert status == 200 and response == LIST_RESULT


@pytest.mark.parametrize('actor,expected', [(a, s) for _, a, s in DENIED], ids=[n for n, _, _ in DENIED])
def test_everyone_else_is_refused_before_any_work(harness, actor, expected):
    harness.actor = actor
    _, status = _call(harness)
    assert status == expected
    assert harness.rpc_calls == [] and harness.method_calls == [], 'nothing may run behind a refusal'


# --- project scope -----------------------------------------------------------------------------------------

def test_unknown_project_is_a_readable_404_and_reads_nothing(harness):
    harness.actor = Actor('super_admin')
    harness.rpc_result = None
    response, status = _call(harness, project_id=424242)
    assert status == 404 and response == {'error': 'Project not found.', 'code': 'NOT_FOUND'}
    assert ('project_get_by_id', 424242) in harness.rpc_calls
    assert harness.method_calls == []


def test_project_lookup_failure_is_a_503_without_the_cause(harness):
    harness.actor = Actor('admin')
    harness.rpc_error = RuntimeError(f'redis down {SECRET_TEXT}')
    response, status = _call(harness)
    assert status == 503 and response['code'] == 'DEPENDENCY_UNAVAILABLE'
    assert SECRET_TEXT not in repr(response) and set(response) == {'error', 'code'}
    assert harness.method_calls == []


def test_the_path_project_is_what_the_method_reads(harness):
    harness.actor = Actor('admin')
    _call(harness, project_id=31)
    ((args, _),) = harness.method_calls
    assert args == (31,)


# --- parameters --------------------------------------------------------------------------------------------

def test_defaults_are_the_credentials_section_first_page(harness):
    harness.actor = Actor('admin')
    _call(harness)
    assert harness.method_calls == [((7,), {'section': 'credentials', 'search': None, 'limit': 20, 'offset': 0})]


def test_parameters_are_passed_through(harness):
    harness.actor = Actor('admin')
    _call(harness, section='credentials', search='git', limit='50', offset='100')
    assert harness.method_calls == [((7,), {'section': 'credentials', 'search': 'git', 'limit': 50, 'offset': 100})]


@pytest.mark.parametrize('args,code', [
    ({'limit': 'abc'}, 'INVALID_LIMIT'),
    ({'limit': '1.5'}, 'INVALID_LIMIT'),
    ({'offset': 'x'}, 'INVALID_OFFSET'),
])
def test_malformed_numbers_are_a_readable_400(harness, args, code):
    harness.actor = Actor('admin')
    response, status = _call(harness, **args)
    assert status == 400 and response['code'] == code and set(response) == {'error', 'code'}
    assert harness.method_calls == []


@pytest.mark.parametrize('code', ['INVALID_SECTION', 'INVALID_LIMIT', 'INVALID_OFFSET', 'INVALID_SEARCH'])
def test_method_refusals_become_a_400_with_a_fixed_message(harness, code):
    harness.actor = Actor('admin')
    harness.method_error = ValueError(code)
    response, status = _call(harness, section='llm')
    assert status == 400 and response['code'] == code and response['error'].endswith('.')
    assert set(response) == {'error', 'code'}


def test_an_unexpected_value_error_is_not_swallowed_or_echoed(harness):
    harness.actor = Actor('admin')
    harness.method_error = ValueError(f'boom {SECRET_TEXT}')
    with pytest.raises(ValueError):
        _call(harness)
