"""#6826: configured Auto classifier, its effective resolution, readiness and write checks.

No Pylon or database: the routing inventory and configuration reads are stubbed; the
settings, readiness, write guard and Administration method are the real modules.
"""
import importlib
import pathlib
import sys
import types

import pytest
from pydantic import ValidationError

ROOT = pathlib.Path(__file__).resolve().parents[2]
PACKAGE = 'configurations_6826_classifier'
PUBLIC = 1


def item(name, project_id=PUBLIC, *, available=True, shared=True, kind='chat', low_tier=False, hint=False,
         display_name=None):
    return {'name': name, 'project_id': project_id, 'available': available, 'shared': shared,
            'low_tier': low_tier, 'display_name': display_name or name.upper(),
            'identity': {'kind': kind, 'low_tier_hint': hint}}


@pytest.fixture
def env(monkeypatch):
    state = types.SimpleNamespace(actor=42, project_admin=True, platform_admin=True, records={}, inventory={},
                                  inventory_calls=[], inventory_error=None, secrets={})

    def get_routing_models(project_id, user_id):
        state.inventory_calls.append((project_id, user_id))
        if state.inventory_error:
            raise state.inventory_error
        return {'items': state.inventory.get(project_id, [])}

    stubs = {
        '': {'__path__': [str(ROOT)]},
        '.models': {'__path__': [str(ROOT / 'models')]},
        '.models.pd': {'__path__': [str(ROOT / 'models/pd')]},
        '.methods': {'__path__': [str(ROOT / 'methods')]},
        '.rpc': {'__path__': [str(ROOT / 'rpc')]},
        '.common_utils': {'get_public_project_id': lambda: PUBLIC},
        '.tracing_access': {'current_actor_id': lambda: state.actor,
                            'is_project_admin': lambda project, actor: state.project_admin,
                            'is_own_personal_project': lambda project, actor: False},
        '.utils_getters': {'get_project_configuration': lambda project, filters: state.records.get(project)},
        '.local_tools': {'log': types.SimpleNamespace(warning=lambda *a, **k: None)},
        '.routing_models': {'get_routing_models': get_routing_models},
        '.utils': {'create_configuration': None, 'update_configuration': None},
    }
    for suffix, values in stubs.items():
        module = types.ModuleType(PACKAGE + suffix)
        module.__dict__.update(values)
        monkeypatch.setitem(sys.modules, module.__name__, module)
    loaded = {name: importlib.import_module(f'{PACKAGE}.{name}') for name in (
        'exceptions', 'models.pd.auto_routing', 'models.pd.environment_settings', 'routing_settings',
        'routing_readiness', 'routing_access', 'methods.routing', 'rpc.routing')}
    monkeypatch.setattr(loaded['routing_settings'], '_project_secrets', lambda project: state.secrets.get(project, {}))
    monkeypatch.setattr(loaded['routing_access'], 'is_platform_admin', lambda actor: state.platform_admin and actor == 42)
    monkeypatch.setattr(loaded['methods.routing'], 'is_platform_admin', lambda actor: state.platform_admin and actor == 42)
    yield types.SimpleNamespace(state=state, error=loaded['exceptions'].ConfigurationError,
        pd=loaded['models.pd.auto_routing'], environment=loaded['models.pd.environment_settings'].EnvironmentSettings,
        settings=loaded['routing_settings'], readiness=loaded['routing_readiness'], guard=loaded['routing_access'],
        method=loaded['methods.routing'], rpc=loaded['rpc.routing'].RPC())
    for name in [m for m in sys.modules if m.startswith(PACKAGE)]:
        del sys.modules[name]


LUNA = {'name': 'gpt-5.6-luna', 'project_id': PUBLIC}
HAIKU = {'name': 'claude-haiku-4-5', 'project_id': 7}


# --- schema -------------------------------------------------------------------------------

def test_defaults_inherit(env):
    assert env.pd.AutoRoutingProjectSettings().classifier is None
    assert env.environment().auto_routing_classifier is None


@pytest.mark.parametrize('value', [
    {'name': '', 'project_id': 1}, {'name': 'x' * 513, 'project_id': 1}, {'name': 'm', 'project_id': 0},
    {'name': 'm', 'project_id': '1'}, {'name': 'm', 'project_id': True}, {'name': 'm'},
    {'name': 'm', 'project_id': 1, 'configuration_id': 3}, 'gpt-5.6-luna',
])
def test_classifier_ref_is_strict(env, value):
    with pytest.raises(ValidationError):
        env.pd.AutoRoutingProjectSettings(classifier=value)
    with pytest.raises(ValidationError):
        env.environment(auto_routing_classifier=value)


# --- effective resolution (#6826 A1) -----------------------------------------------------

LOW = {'name': 'team-small', 'project_id': 7}
LOW_SECRETS = {'default_llm_low_tier_model_name': 'team-small', 'default_llm_low_tier_model_project_id': '7'}


def _effective(env, platform=None, project=None, low_tier=None, items=None):
    environment = {'data': {'auto_routing_available': True, 'auto_routing_project_default': True,
                            'auto_routing_classifier': platform}}
    return env.settings.effective_settings(environment, {'data': {'classifier': project}}, low_tier, items)


def test_unchecked_order_is_project_then_low_tier_then_platform(env):
    assert _effective(env, LUNA, HAIKU, LOW)['classifier'] == {**HAIKU, 'source': 'project'}
    assert _effective(env, LUNA, None, LOW)['classifier'] == {**LOW, 'source': 'project_low_tier'}
    assert _effective(env, LUNA, None, None)['classifier'] == {**LUNA, 'source': 'platform'}
    assert _effective(env)['classifier'] is None


def test_each_level_is_used_only_when_available_chat(env):
    items = [item('claude-haiku-4-5', project_id=7, available=False), item('team-small', project_id=7),
             item('gpt-5.6-luna')]
    assert _effective(env, LUNA, HAIKU, LOW, items)['classifier'] == {**LOW, 'source': 'project_low_tier'}
    items[1] = item('team-small', project_id=7, kind='embedding')
    assert _effective(env, LUNA, HAIKU, LOW, items)['classifier'] == {**LUNA, 'source': 'platform'}
    assert _effective(env, LUNA, HAIKU, LOW, [])['classifier'] is None


def test_malformed_levels_are_skipped(env):
    assert _effective(env, LUNA, {'name': 'x', 'project_id': 'one'}, {'name': ''})['classifier']['source'] == 'platform'
    assert _effective(env, {'name': ''}, None)['classifier'] is None


@pytest.mark.parametrize('secrets,expected', [
    (LOW_SECRETS, LOW), ({**LOW_SECRETS, 'default_llm_low_tier_model_project_id': 7}, LOW),
    ({'default_llm_low_tier_model_name': 'team-small'}, None), ({}, None), (None, None),
    ({**LOW_SECRETS, 'default_llm_low_tier_model_project_id': 'seven'}, None),
])
def test_low_tier_ref_from_vault_keys(env, secrets, expected):
    assert env.settings.low_tier_ref(secrets) == expected


def test_revision_covers_resolved_classifier(env):
    items = [item('team-small', project_id=7), item('gpt-5.6-luna')]
    revisions = {_effective(env, LUNA, None, LOW, items)['revision'],
                 _effective(env, LUNA, None, LOW, items[1:])['revision'],
                 _effective(env, LUNA, HAIKU)['revision'], _effective(env)['revision']}
    assert len(revisions) == 4


def test_settings_rpc_resolves_for_actor_when_given(env):
    env.state.records = {PUBLIC: {'data': {'auto_routing_available': True, 'auto_routing_classifier': LUNA}},
                         7: {'data': {'enabled': True}}}
    env.state.secrets = {7: LOW_SECRETS}
    assert env.settings.get_effective_settings(7)['classifier'] == {**LOW, 'source': 'project_low_tier'}
    assert env.state.inventory_calls == []
    env.state.inventory = {7: [item('gpt-5.6-luna')]}
    assert env.rpc.configurations_get_auto_routing_settings(7, 42)['classifier'] == {**LUNA, 'source': 'platform'}
    assert env.state.inventory_calls == [(7, 42)]


# --- readiness ----------------------------------------------------------------------------

def _state(env, *, enabled=True, project=None, low_tier=None, platform=None, inventory=()):
    env.state.records = {PUBLIC: {'data': {'auto_routing_available': True, 'auto_routing_project_default': enabled,
                                           'auto_routing_classifier': platform}},
                         7: {'data': {'classifier': project}}}
    env.state.secrets = {7: low_tier or {}}
    env.state.inventory = {7: list(inventory)}
    return env.readiness.get_auto_routing_readiness(7, 42)


def test_ready_with_low_tier_default(env):
    result = _state(env, low_tier=LOW_SECRETS, platform=LUNA,
                    inventory=[item('team-small', project_id=7, display_name='Team small'), item('gpt-5.6-luna')])
    assert result == {'ready': True, 'reasons': [],
        'classifier': {**LOW, 'display_name': 'Team small', 'source': 'project_low_tier', 'available': True},
        'default_classifier': {**LOW, 'display_name': 'Team small', 'source': 'project_low_tier'}}


def test_explicit_classifier_still_reports_default(env):
    result = _state(env, project=HAIKU, platform=LUNA,
                    inventory=[item('claude-haiku-4-5', project_id=7, display_name='Haiku'), item('gpt-5.6-luna', display_name='Luna')])
    assert result['ready'] is True and result['classifier']['source'] == 'project'
    assert result['default_classifier'] == {**LUNA, 'display_name': 'Luna', 'source': 'platform'}


def test_unavailable_low_tier_falls_through_to_platform(env):
    result = _state(env, low_tier=LOW_SECRETS, platform=LUNA,
                    inventory=[item('team-small', project_id=7, available=False), item('gpt-5.6-luna')])
    assert result['ready'] is True and result['reasons'] == []
    assert result['classifier']['source'] == 'platform'
    assert result['default_classifier']['source'] == 'platform'


def test_nothing_set_is_not_configured_without_inventory_read(env):
    result = _state(env)
    assert result == {'ready': False, 'classifier': None, 'default_classifier': None, 'reasons': [
        {'code': 'CLASSIFIER_NOT_CONFIGURED', 'message': 'No Auto classifier model is configured'}]}
    assert env.state.inventory_calls == []


@pytest.mark.parametrize('inventory,code', [
    ([], 'CLASSIFIER_UNAVAILABLE'),
    ([item('claude-haiku-4-5', project_id=7, available=False)], 'CLASSIFIER_UNAVAILABLE'),
    ([item('claude-haiku-4-5', project_id=8)], 'CLASSIFIER_UNAVAILABLE'),
    ([item('claude-haiku-4-5', project_id=7, kind='embedding')], 'CLASSIFIER_NOT_CHAT'),
    ([{**item('claude-haiku-4-5', project_id=7), 'identity': None}], 'CLASSIFIER_NOT_CHAT'),
])
def test_set_but_unusable_reports_highest_priority_ref(env, inventory, code):
    result = _state(env, project=HAIKU, low_tier=LOW_SECRETS, platform=LUNA, inventory=inventory)
    assert result['ready'] is False
    assert [(reason['code'], reason['model']) for reason in result['reasons']] == [(code, HAIKU)]
    assert 'claude-haiku-4-5' in result['reasons'][0]['message']
    assert result['classifier'] == {**HAIKU, 'display_name': result['classifier']['display_name'],
                                    'source': 'project', 'available': False}
    assert result['default_classifier'] is None


def test_unavailable_message(env):
    result = _state(env, platform=LUNA, inventory=[item('gpt-5.6-luna', available=False)])
    assert result['reasons'] == [{'code': 'CLASSIFIER_UNAVAILABLE', 'model': LUNA,
        'message': 'Classifier model gpt-5.6-luna is no longer available to this project'}]
    assert result['classifier']['source'] == 'platform'


def test_disabled_auto_is_reported_alone_when_classifier_resolves(env):
    result = _state(env, enabled=False, platform=LUNA, inventory=[item('gpt-5.6-luna')])
    assert result['ready'] is False
    assert [reason['code'] for reason in result['reasons']] == ['AUTO_DISABLED']
    assert result['classifier']['available'] is True


def test_unreadable_inventory_fails_closed(env):
    env.state.inventory_error = RuntimeError('visibility unavailable')
    result = _state(env, low_tier=LOW_SECRETS, platform=LUNA)
    assert [(reason['code'], reason['model']) for reason in result['reasons']] == [('CLASSIFIER_UNAVAILABLE', LOW)]


def test_missing_actor_fails_closed(env):
    env.state.records = {PUBLIC: {'data': {'auto_routing_available': True, 'auto_routing_project_default': True,
                                           'auto_routing_classifier': LUNA}}}
    env.state.inventory_error = ValueError('Routing inventory requires a trusted project and user ID')
    result = env.readiness.get_auto_routing_readiness(7, None)
    assert result['ready'] is False and result['reasons'][0]['code'] == 'CLASSIFIER_UNAVAILABLE'


def test_readiness_rpc(env):
    _state(env, platform=LUNA, inventory=[item('gpt-5.6-luna')])
    result = env.rpc.configurations_get_auto_routing_readiness(7, 42)
    assert result['ready'] is True
    assert result['default_classifier'] == {**LUNA, 'display_name': 'GPT-5.6-LUNA', 'source': 'platform'}


# --- project write validation -------------------------------------------------------------

def _project_write(env, data, old=None):
    return env.guard.validate_routing_write('auto_routing', 7, {'data': data},
                                            existing={'data': old or {}, 'elitea_title': 'auto_routing'})


def test_project_accepts_visible_chat_classifier(env):
    env.state.inventory = {7: [item('claude-haiku-4-5', project_id=7, shared=False)]}
    assert _project_write(env, {'enabled': True, 'classifier': HAIKU})['data'] == {'enabled': True, 'classifier': HAIKU}
    assert env.state.inventory_calls == [(7, 42)]


@pytest.mark.parametrize('inventory', [
    [], [item('claude-haiku-4-5', project_id=7, available=False)],
    [item('claude-haiku-4-5', project_id=7, kind='image')], [item('claude-haiku-4-5', project_id=8)],
])
def test_project_refuses_invisible_unavailable_or_non_chat(env, inventory):
    env.state.inventory = {7: inventory}
    with pytest.raises(env.error) as caught:
        _project_write(env, {'enabled': True, 'classifier': HAIKU})
    assert caught.value.field == 'classifier'
    assert caught.value.message == 'Model claude-haiku-4-5 is not available to this project'


def test_unchanged_stale_classifier_is_accepted_changed_invalid_is_refused(env):
    env.state.inventory = {7: []}
    old = {'enabled': True, 'classifier': HAIKU}
    assert _project_write(env, {'enabled': False, 'classifier': HAIKU}, old)['data'] == {'enabled': False, 'classifier': HAIKU}
    assert env.state.inventory_calls == []
    with pytest.raises(env.error, match='not available'):
        _project_write(env, {'enabled': False, 'classifier': {**HAIKU, 'name': 'other'}}, old)


def test_null_is_always_accepted_and_omitted_is_preserved(env):
    old = {'enabled': True, 'classifier': HAIKU}
    assert _project_write(env, {'enabled': True, 'classifier': None}, old)['data']['classifier'] is None
    assert _project_write(env, {'enabled': False}, old)['data'] == {'enabled': False, 'classifier': HAIKU}
    assert env.state.inventory_calls == []


def test_project_classifier_still_requires_project_admin(env):
    env.state.project_admin = False
    env.state.inventory = {7: [item('claude-haiku-4-5', project_id=7)]}
    with pytest.raises(env.error, match='project admins'):
        _project_write(env, {'classifier': HAIKU})


def test_malformed_project_classifier_is_a_configuration_error(env):
    with pytest.raises(env.error):
        _project_write(env, {'classifier': {'name': 'm', 'project_id': 7, 'extra': 1}})


# --- platform write validation ------------------------------------------------------------

def _platform_write(env, data, old=None, project_id=PUBLIC, title='environment_settings'):
    return env.guard.validate_routing_write('environment_settings', project_id, {'data': data, 'elitea_title': title},
                                            existing={'data': old or {}, 'elitea_title': 'environment_settings'})


def test_platform_accepts_shared_public_chat_model(env):
    env.state.inventory = {PUBLIC: [item('gpt-5.6-luna')]}
    result = _platform_write(env, {'auto_routing_classifier': LUNA})
    assert result['data']['auto_routing_classifier'] == LUNA
    assert env.state.inventory_calls == [(PUBLIC, 42)]


@pytest.mark.parametrize('inventory', [[], [item('gpt-5.6-luna', shared=False)], [item('gpt-5.6-luna', kind='audio')],
                                       [item('gpt-5.6-luna', available=False)]])
def test_platform_refuses_private_unavailable_or_non_chat(env, inventory):
    env.state.inventory = {PUBLIC: inventory}
    with pytest.raises(env.error) as caught:
        _platform_write(env, {'auto_routing_classifier': LUNA})
    assert caught.value.field == 'classifier'


def test_platform_classifier_change_requires_administration_admin(env):
    env.state.inventory = {PUBLIC: [item('gpt-5.6-luna')]}
    env.state.platform_admin = False
    with pytest.raises(env.error, match='Administration admins'):
        _platform_write(env, {'auto_routing_classifier': LUNA})
    env.state.platform_admin = True
    with pytest.raises(env.error, match='Administration admins'):
        _platform_write(env, {'auto_routing_classifier': LUNA}, project_id=7)


def test_platform_classifier_requires_environment_settings_record(env):
    env.state.inventory = {PUBLIC: [item('gpt-5.6-luna')]}
    with pytest.raises(env.error, match='environment_settings record'):
        _platform_write(env, {'auto_routing_classifier': LUNA}, title='other')


def test_platform_unchanged_stale_classifier_does_not_block_flags(env):
    env.state.inventory = {PUBLIC: []}
    old = {'auto_routing_available': False, 'auto_routing_classifier': LUNA}
    result = _platform_write(env, {'auto_routing_available': True, 'auto_routing_classifier': LUNA}, old)
    assert result['data']['auto_routing_available'] is True
    assert env.state.inventory_calls == []
    with pytest.raises(env.error, match='not available'):
        _platform_write(env, {'auto_routing_classifier': {'name': 'other', 'project_id': PUBLIC}}, old)


def test_legacy_environment_save_preserves_classifier_without_actor(env):
    env.state.actor = None
    old = {'auto_routing_classifier': LUNA}
    result = _platform_write(env, {'system_sender_name': 'Company'}, old)
    assert result['data']['auto_routing_classifier'] == LUNA


def test_deleting_environment_with_classifier_requires_admin(env):
    env.state.actor = None
    with pytest.raises(env.error, match='Administration admins'):
        env.guard.validate_routing_write('environment_settings', PUBLIC, {}, deleting=True, existing={
            'data': {'auto_routing_classifier': LUNA}, 'elitea_title': 'environment_settings'})


# --- Administration method ----------------------------------------------------------------

@pytest.fixture
def admin(env, monkeypatch):
    writes = []
    env.state.records = {PUBLIC: {'id': 9, 'data': {'system_sender_name': 'Company'}}}
    env.state.inventory = {PUBLIC: [
        item('gpt-5.6-luna', hint=True, display_name='Luna'), item('gpt-5.4', display_name='GPT 5.4'),
        item('team-small', low_tier=True, display_name='Team small'), item('private-mini', shared=False, hint=True),
        item('embed-mini', kind='embedding', hint=True),
    ]}

    def update(project_id, config_id, payload):
        writes.append((project_id, config_id, payload))
        return {'id': config_id, **payload}
    monkeypatch.setattr(env.method, 'update_configuration', update)
    return types.SimpleNamespace(method=env.method.Method(), writes=writes)


def test_platform_payload_returns_classifier_and_low_tier_options(env, admin):
    result = admin.method.auto_routing_platform_settings()
    assert result == {'available': False, 'project_default': False, 'classifier': None, 'can_manage': True,
        'classifier_options': [
            {'name': 'gpt-5.6-luna', 'project_id': PUBLIC, 'display_name': 'Luna', 'low_tier': True},
            {'name': 'team-small', 'project_id': PUBLIC, 'display_name': 'Team small', 'low_tier': True},
        ]}


def test_platform_options_fall_back_to_all_chat_models(env, admin):
    env.state.inventory = {PUBLIC: [item('gpt-5.4', display_name='B'), item('gpt-5.5', display_name='A'),
                                    item('private', shared=False)]}
    options = admin.method.auto_routing_platform_settings()['classifier_options']
    assert [(option['name'], option['low_tier']) for option in options] == [('gpt-5.5', False), ('gpt-5.4', False)]


def test_platform_put_with_classifier(env, admin):
    result = admin.method.auto_routing_platform_settings({'available': True, 'project_default': False, 'classifier': LUNA})
    assert admin.writes == [(PUBLIC, 9, {'data': {'system_sender_name': 'Company', 'auto_routing_available': True,
        'auto_routing_project_default': False, 'auto_routing_classifier': LUNA}})]
    assert result['classifier'] == LUNA and result['available'] is True


def test_platform_put_without_classifier_keeps_stored_value(env, admin):
    env.state.records[PUBLIC]['data']['auto_routing_classifier'] = LUNA
    result = admin.method.auto_routing_platform_settings({'available': True, 'project_default': True})
    assert 'auto_routing_classifier' in admin.writes[0][2]['data']
    assert admin.writes[0][2]['data']['auto_routing_classifier'] == LUNA
    assert result['classifier'] == LUNA


def test_platform_put_null_classifier_clears_it(env, admin):
    env.state.records[PUBLIC]['data']['auto_routing_classifier'] = LUNA
    admin.method.auto_routing_platform_settings({'available': True, 'project_default': True, 'classifier': None})
    assert admin.writes[0][2]['data']['auto_routing_classifier'] is None


@pytest.mark.parametrize('values', [
    {'available': True}, {'available': True, 'classifier': LUNA},
    {'available': 'true', 'project_default': False}, {'available': True, 'project_default': False, 'author_id': 42},
])
def test_platform_put_rejects_bad_flags(env, admin, values):
    with pytest.raises(ValueError):
        admin.method.auto_routing_platform_settings(values)
    assert admin.writes == []


def test_platform_method_requires_administration_admin(env, admin):
    env.state.platform_admin = False
    with pytest.raises(PermissionError):
        admin.method.auto_routing_platform_settings()
