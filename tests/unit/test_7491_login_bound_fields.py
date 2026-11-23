"""#7491: an edit of the settings a stored login was issued for removes that login.

A Dataverse credential kept its refresh token after its client ID or secret changed, so Login refreshed
the old token with the new client and showed Microsoft's client error instead of the sign-in. Now a
write that keeps the old token but changes a bound field (URL, tenant, client, secret, authority) drops
it; Login, Logout and token rotation writes, which set or clear the token themselves, are left alone.
"""
import pytest

from .test_6891_configuration_updated_event import _stored, utils  # noqa: F401  (fixture reuse)

DATAVERSE = {'url': 'https://org.crm4.dynamics.com', 'tenant_id': 't1', 'client_id': 'c1',
             'client_secret': '{{secret.cs}}', 'refresh_token': '{{secret.rt}}'}
VAULT = {'{{secret.cs}}': 'secret-1', '{{secret.rt}}': 'rt-1'}


def unsecret(reference):
    return VAULT[reference]


@pytest.mark.parametrize('field, value', [
    ('url', 'https://other.crm4.dynamics.com'),
    ('tenant_id', 't2'),
    ('client_id', 'c2'),
    ('client_secret', 'secret-2'),
    ('client_secret', '{{secret.cs2}}'),
])
def test_7491_changing_a_bound_field_drops_the_login(utils, field, value):
    new = {**DATAVERSE, field: value}
    assert utils.drop_unbound_login(DATAVERSE, new, unsecret) == '{{secret.rt}}'
    assert 'refresh_token' not in new


@pytest.mark.parametrize('new', [
    dict(DATAVERSE),                                       # saved unchanged
    {**DATAVERSE, 'client_secret': 'secret-1'},            # same secret retyped
    {**DATAVERSE, 'client_id': ' c1 '},                    # whitespace only
    {**DATAVERSE, 'refresh_token': '{{secret.rt2}}', 'client_id': 'c2'},  # Login with the new client
    {k: v for k, v in DATAVERSE.items() if k != 'refresh_token'},         # Logout
])
def test_7491_login_kept_or_set_by_the_write_itself(utils, new):
    before = dict(new)
    assert utils.drop_unbound_login(DATAVERSE, new, unsecret) is None
    assert new == before


def test_7491_other_fields_and_credentials_without_login(utils):
    outlook = {'client_id': 'c1', 'oauth_discovery_endpoint': 'https://login.microsoftonline.com/t1',
               'mailbox': 'a@x', 'scopes': ['Mail.Read'], 'refresh_token': '{{secret.rt}}'}
    assert utils.drop_unbound_login(outlook, {**outlook, 'mailbox': 'b@x', 'scopes': ['Mail.Send']}) is None
    authority = {**outlook, 'oauth_discovery_endpoint': 'https://login.microsoftonline.com/t2'}
    assert utils.drop_unbound_login(outlook, authority) == '{{secret.rt}}'
    no_login = {k: v for k, v in DATAVERSE.items() if k != 'refresh_token'}
    assert utils.drop_unbound_login(no_login, {**no_login, 'client_id': 'c2'}) is None


def test_7491_unreadable_secret_counts_as_changed(utils):
    def broken(_reference):
        raise RuntimeError('vault down')
    new = {**DATAVERSE, 'client_secret': 'secret-1'}
    assert utils.drop_unbound_login(DATAVERSE, new, broken) == '{{secret.rt}}'


def test_7491_update_stores_the_edit_without_the_login_and_purges_it(utils, monkeypatch):
    purged = []
    monkeypatch.setattr(utils, 'purge_secrets', lambda project_id, secrets_to_delete: purged.append(
        (project_id, set(secrets_to_delete))))
    _stored('dataverse', 'credentials', dict(DATAVERSE))

    result = utils.update_configuration(2, 4, {'data': {**DATAVERSE, 'client_id': 'c2'}})

    assert 'refresh_token' not in result['data']
    assert result['data']['client_id'] == 'c2'
    assert purged == [(2, {'{{secret.rt}}'})]


def test_7491_update_that_keeps_the_settings_keeps_the_login(utils, monkeypatch):
    purged = []
    monkeypatch.setattr(utils, 'purge_secrets', lambda **kwargs: purged.append(kwargs))
    _stored('dataverse', 'credentials', dict(DATAVERSE))

    result = utils.update_configuration(2, 4, {'data': dict(DATAVERSE)})

    assert result['data']['refresh_token'] == '{{secret.rt}}'
    assert purged == []
