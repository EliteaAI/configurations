"""Web Search project settings guard (#6982): shape and ownership, no Pylon or database."""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from test_auto_routing import modules  # noqa: F401  (fixture reuse)


def _admin(modules, monkeypatch, is_admin=True, personal=False):
    access = modules.guard
    monkeypatch.setattr(access, 'current_actor_id', lambda: 7)
    monkeypatch.setattr(access, 'is_project_admin', lambda project, actor: is_admin)
    monkeypatch.setattr(access, 'is_own_personal_project', lambda project, actor: personal)


def _payload(**data):
    return {'type': 'web_search_settings', 'elitea_title': 'web_search_settings', 'shared': False, 'data': data}


PUBLIC = 1  # the fixture's get_public_project_id


def test_public_admin_sets_platform_toolkit(modules, monkeypatch):
    _admin(modules, monkeypatch)
    out = modules.guard.validate_routing_write('web_search_settings', PUBLIC, _payload(toolkit_id=42))
    assert out['data'] == {'toolkit_id': 42}


def test_null_turns_the_toggle_off(modules, monkeypatch):
    _admin(modules, monkeypatch)
    out = modules.guard.validate_routing_write('web_search_settings', PUBLIC, _payload(toolkit_id=None))
    assert out['data'] == {'toolkit_id': None}


@pytest.mark.parametrize('project_id', [3, 25])
def test_other_projects_cannot_set_it_even_as_admin(modules, monkeypatch, project_id):
    # KISS (owner): teams that want their own search add their own toolkit to the chat explicitly
    _admin(modules, monkeypatch, personal=True)
    with pytest.raises(modules.error):
        modules.guard.validate_routing_write('web_search_settings', project_id, _payload(toolkit_id=1))


@pytest.mark.parametrize('payload,field', [
    ({**_payload(), 'shared': True}, 'shared'),
    ({**_payload(), 'elitea_title': 'other'}, 'elitea_title'),
    (_payload(toolkit_id='42'), 'data'),
    (_payload(toolkit_id=0), 'data'),
    (_payload(toolkit_id=1, extra=1), 'data'),
])
def test_rejects_bad_shape(modules, monkeypatch, payload, field):
    _admin(modules, monkeypatch)
    with pytest.raises(modules.error):
        modules.guard.validate_routing_write('web_search_settings', PUBLIC, payload)


def test_non_admin_refused(modules, monkeypatch):
    _admin(modules, monkeypatch, is_admin=False)
    with pytest.raises(modules.error):
        modules.guard.validate_routing_write('web_search_settings', PUBLIC, _payload(toolkit_id=1))


def test_no_actor_refused(modules):
    with pytest.raises(modules.error):
        modules.guard.validate_routing_write('web_search_settings', PUBLIC, _payload(toolkit_id=1))


def test_a_stale_row_outside_the_public_project_can_still_be_deleted(modules):
    out = modules.guard.validate_routing_write('web_search_settings', 3, {}, deleting=True)
    assert out == {}
