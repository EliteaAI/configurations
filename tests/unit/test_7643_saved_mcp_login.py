"""#7643: server-side save / clear of a credential's saved login (configurations/utils.py).

Only login fields are written; the rotation write-back is skipped when the login was re-done or
cleared meanwhile; a concurrent edit (generation mismatch) is a soft False; log out drops the token,
the account and a DCR client (useless without the login), and purges the replaced vault secrets.
"""
import pytest

from .test_6891_configuration_updated_event import PACKAGE, utils  # noqa: F401  (fixture reuse)

import sys


@pytest.fixture
def env(utils, monkeypatch):
    state = type('S', (), {})()
    state.rows = {'uuid-m': (9, 4, {'url': 'https://mcp', 'refresh_token': '{{secret.rt}}', 'client_id': 'c',
                                    'used_dcr': True, 'account': 'bot'})}
    state.updates, state.purged, state.vault = [], [], {'{{secret.rt}}': 'rt-old'}
    state.conflict = False
    monkeypatch.setattr(utils, '_find_login_configuration',
                        lambda project_id, uuid_, type_: (lambda r: (r[0], r[1], dict(r[2])) if r else None)(
                            state.rows.get(uuid_)) if type_ == 'mcp_login' else None)

    def update(project_id, config_id, payload, expected_generation=None):
        if state.conflict:
            raise sys.modules[f'{PACKAGE}.exceptions'].ConfigurationError('generation', 'modified')
        state.updates.append((project_id, config_id, payload, expected_generation))
    monkeypatch.setattr(utils, 'update_configuration', update)
    monkeypatch.setattr(utils, 'purge_secrets', lambda project_id, secrets_to_delete: state.purged.append(
        set(secrets_to_delete)))

    class Vault:
        def __init__(self, project_id):
            pass

        def unsecret(self, data):
            return {k: state.vault.get(v, v) for k, v in data.items()}
    monkeypatch.setattr(utils, 'VaultClient', Vault)
    state.utils = utils
    return state


def test_7643_save_writes_only_login_fields_with_generation_guard(env):
    assert env.utils.save_stored_login(3, 'uuid-m', 'mcp_login', {
        'refresh_token': 'rt-new', 'account': 'bot2', 'url': 'https://evil', 'client_secret': None}) is True

    [(project_id, config_id, payload, generation)] = env.updates
    assert (project_id, config_id, generation) == (3, 9, 4)
    assert payload['data'] == {'url': 'https://mcp', 'refresh_token': 'rt-new', 'client_id': 'c',
                               'used_dcr': True, 'account': 'bot2'}
    assert env.purged == [{'{{secret.rt}}'}]


def test_7643_rotation_write_back_skipped_when_login_changed(env):
    assert env.utils.save_stored_login(3, 'uuid-m', 'mcp_login', {'refresh_token': 'rt-new'},
                                       expected_refresh_token='rt-someone-else-saw') is False
    assert env.updates == []


def test_7643_rotation_write_back_when_login_unchanged(env):
    assert env.utils.save_stored_login(3, 'uuid-m', 'mcp_login', {'refresh_token': 'rt-new'},
                                       expected_refresh_token='rt-old') is True
    assert env.updates[0][2]['data']['refresh_token'] == 'rt-new'


def test_7643_concurrent_edit_is_a_soft_false(env):
    env.conflict = True
    assert env.utils.save_stored_login(3, 'uuid-m', 'mcp_login', {'refresh_token': 'x'}) is False


@pytest.mark.parametrize('uuid_, type_', [('uuid-x', 'mcp_login'), ('uuid-m', 'outlook')])
def test_7643_unknown_or_other_type_is_not_written(env, uuid_, type_):
    assert env.utils.save_stored_login(3, uuid_, type_, {'refresh_token': 'x'}) is False
    assert env.utils.clear_stored_login(3, uuid_, type_) is False
    assert env.updates == []


def test_7643_log_out_drops_token_account_and_dcr_client(env):
    assert env.utils.clear_stored_login(3, 'uuid-m', 'mcp_login') is True
    assert env.updates[0][2]['data'] == {'url': 'https://mcp'}
    assert env.purged == [{'{{secret.rt}}'}]


def test_7643_log_out_keeps_a_configured_client(env):
    env.rows['uuid-m'][2].pop('used_dcr')
    env.utils.clear_stored_login(3, 'uuid-m', 'mcp_login')
    assert env.updates[0][2]['data'] == {'url': 'https://mcp', 'client_id': 'c'}


def test_7643_stored_login_fields_cover_mcp(env):
    assert {'token_endpoint', 'resource', 'used_dcr', 'account'} <= set(env.utils.STORED_LOGIN_FIELDS)


# --- code-review fixes -------------------------------------------------------------------------------------------

def test_7643_server_side_save_refuses_other_types(env, monkeypatch):
    monkeypatch.setattr(env.utils, '_find_login_configuration', lambda *a: (1, 1, {'refresh_token': 'x'}))
    assert env.utils.save_stored_login(3, 'uuid-o', 'outlook', {'refresh_token': 'y', 'url': 'z'}) is False
    assert env.updates == []


def test_7643_rotation_writes_only_the_refresh_token_for_any_type(env, monkeypatch):
    monkeypatch.setattr(env.utils, '_find_login_configuration',
                        lambda *a: (1, 2, {'refresh_token': '{{secret.rt}}', 'client_id': 'c'}))
    assert env.utils.save_stored_login(3, 'uuid-o', 'outlook', {'refresh_token': 'rt-new', 'client_id': 'evil'},
                                       expected_refresh_token='rt-old') is True
    assert env.updates[0][2]['data'] == {'refresh_token': 'rt-new', 'client_id': 'c'}


def test_7643_log_out_retries_once_after_a_concurrent_rotation(env):
    calls = []
    original = env.utils.update_configuration

    def flaky(project_id, config_id, payload, expected_generation=None):
        calls.append(expected_generation)
        if len(calls) == 1:
            raise sys.modules[f'{PACKAGE}.exceptions'].ConfigurationError('generation', 'modified')
        env.updates.append((project_id, config_id, payload, expected_generation))
    env.utils.update_configuration = flaky
    try:
        assert env.utils.clear_stored_login(3, 'uuid-m', 'mcp_login') is True
    finally:
        env.utils.update_configuration = original
    assert len(calls) == 2


def test_7643_log_out_gives_up_after_a_second_conflict(env):
    env.conflict = True
    assert env.utils.clear_stored_login(3, 'uuid-m', 'mcp_login') is False


def test_7643_expansion_never_carries_the_saved_login_secrets(env, monkeypatch):
    class Cfg:
        type, project_id, uuid, generation, elitea_title = 'mcp_login', 3, 'uuid-m', 4, 'bot'
        data = {'url': 'https://mcp', 'refresh_token': '{{secret.rt}}', 'client_secret': '{{secret.cs}}'}
    payload = {}
    monkeypatch.setattr(env.utils, 'resolve_on_use', lambda *a: None)
    monkeypatch.setattr(env.utils, 'apply_oauth_app', lambda *a: None)
    env.utils._merge_configuration(payload, Cfg, public_project_id=1, current_project_id=3, unsecret=True)

    assert 'refresh_token' not in payload and 'client_secret' not in payload
    assert payload['saved_login'] is True and payload['url'] == 'https://mcp'
