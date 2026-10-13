"""#6722: Auto routing policy as platform/project configuration.

Same harness as test_6826_classifier_settings: no Pylon or database, the routing inventory and
configuration reads are stubbed, the schema, merge, readiness and write guard are the real modules.
"""
import copy
import importlib
import json
import pathlib
import sys
import types

import pytest
from pydantic import ValidationError

ROOT = pathlib.Path(__file__).resolve().parents[2]
PACKAGE = 'configurations_6722_policy'
PUBLIC = 1
DEFAULTS = {
    'version': 1, 'shadow_mode': True, 'selector': {'artifact': None, 'tau': 0.85}, 'pool': None, 'fallback': None,
    'output': {'default_allowance': 8000, 'min_cap': 256, 'max_cap': 32000},
    'thinking_budget_tokens': {'low': 2048, 'medium': 4096, 'high': 9092},
    'greeting_rule': True, 'classifier': {'enabled': False},
}
GPT54 = {'name': 'gpt-5.4', 'project_id': PUBLIC}
TEAM = {'name': 'team-model', 'project_id': 7}
LUNA = {'name': 'gpt-5.6-luna', 'project_id': PUBLIC}


def item(name, project_id=PUBLIC, *, available=True, shared=True, kind='chat'):
    return {'name': name, 'project_id': project_id, 'available': available, 'shared': shared,
            'low_tier': False, 'display_name': name.upper(), 'identity': {'kind': kind, 'low_tier_hint': False}}


@pytest.fixture
def env(monkeypatch):
    state = types.SimpleNamespace(actor=42, project_admin=True, platform_admin=True, records={}, inventory={},
                                  inventory_calls=[], secrets={})

    def get_routing_models(project_id, user_id):
        state.inventory_calls.append((project_id, user_id))
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


def platform(**policy):
    """Platform record whose stored policy is the defaults with ``policy`` laid over them (validated shape)."""
    return {'data': {'auto_routing_policy': policy}}


def full(env, **overrides):
    return {**env.pd.AutoRoutingPolicy().model_dump(mode='json'), **overrides}


# --- schema --------------------------------------------------------------------------------

def test_defaults_equal_todays_literals(env):
    assert env.pd.AutoRoutingPolicy().model_dump(mode='json') == DEFAULTS
    assert env.environment().auto_routing_policy is None
    assert env.environment().auto_routing_policy_allow_project_activation is False
    assert env.pd.AutoRoutingProjectSettings().policy is None


def _bad_policies():
    base = copy.deepcopy(DEFAULTS)

    def with_(path, value):
        data = copy.deepcopy(base)
        target = data
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value
        return data

    cases = [with_(['version'], 2), with_(['shadow_mode'], 'true'), with_(['shadow_mode'], None),
             with_(['greeting_rule'], 1), with_(['classifier', 'enabled'], 'no'), {**base, 'unknown': 1},
             with_(['selector', 'extra'], 1), with_(['output', 'extra'], 1), with_(['classifier', 'extra'], 1),
             with_(['thinking_budget_tokens', 'extra'], 1), with_(['fallback'], {'name': 'm', 'project_id': 1, 'x': 1}),
             with_(['fallback'], 'gpt-5.4'), with_(['selector', 'artifact'], ''), with_(['selector', 'artifact'], '../x'),
             with_(['selector', 'artifact'], 'a' * 129)]
    cases += [with_(['selector', 'tau'], v) for v in (0.49, 1.0, 0.0, True, '0.9', None)]
    cases += [with_(['output', 'default_allowance'], v) for v in (255, 128001, True, 8000.5, None)]
    cases += [with_(['output', 'min_cap'], v) for v in (0, 128001)]
    cases += [with_(['output', 'max_cap'], v) for v in (0, 128001, '32000')]
    cases += [with_(['output'], {'default_allowance': 8000, 'min_cap': 40000, 'max_cap': 32000})]
    cases += [with_(['thinking_budget_tokens', key], v) for key in ('low', 'medium', 'high') for v in (-1, 128001, '1', True)]
    cases += [with_(['pool'], v) for v in ([], [''], ['a' * 513], [1], ['m'] * 257, 'gpt-5.4')]
    return cases


@pytest.mark.parametrize('policy', _bad_policies())
def test_every_policy_bound_is_enforced(env, policy):
    with pytest.raises(ValidationError):
        env.pd.AutoRoutingPolicy.model_validate(policy)
    with pytest.raises(ValidationError):
        env.environment(auto_routing_policy=policy)


@pytest.mark.parametrize('path,value', [
    (('selector', 'tau'), 0.5), (('selector', 'tau'), 0.99), (('selector', 'tau'), 1 / 2),
    (('output', 'default_allowance'), 256), (('output', 'default_allowance'), 128000),
    (('output', 'min_cap'), 1), (('output', 'max_cap'), 128000), (('thinking_budget_tokens', 'low'), 0),
    (('thinking_budget_tokens', 'high'), 128000), (('selector', 'artifact'), 'selector-v5-r1'),
    (('pool',), ['gpt-5.4']), (('pool',), ['m'] * 256), (('fallback',), GPT54),
])
def test_boundary_values_are_accepted(env, path, value):
    data = copy.deepcopy(DEFAULTS)
    target = data
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    assert env.pd.AutoRoutingPolicy.model_validate(data)


@pytest.mark.parametrize('override', [
    {'shadow_mode': None}, {'greeting_rule': None}, {'selector': {'tau': None}}, {'selector': {'tau': 0.4}},
    {'output': {'max_cap': 0}}, {'output': {'min_cap': 9, 'max_cap': 3}}, {'pool': []}, {'version': 1},
    {'thinking_budget_tokens': {'low': -1}}, {'classifier': {'enabled': None}}, {'unknown': 1},
    {'selector': {'extra': 1}}, {'fallback': {'name': 'x'}}, {'selector': None}, {'output': None},
])
def test_override_is_partial_but_still_strict(env, override):
    with pytest.raises(ValidationError):
        env.pd.AutoRoutingPolicyOverride.model_validate(override)
    with pytest.raises(ValidationError):
        env.pd.AutoRoutingProjectSettings(policy=override)


def test_override_distinguishes_inherit_from_explicit_null(env):
    parsed = env.pd.AutoRoutingPolicyOverride.model_validate({'pool': None, 'fallback': None, 'selector': {'artifact': None}})
    assert parsed.model_dump(exclude_unset=True) == {'pool': None, 'fallback': None, 'selector': {'artifact': None}}
    assert env.pd.AutoRoutingPolicyOverride().model_dump(exclude_unset=True) == {}


# --- merge ---------------------------------------------------------------------------------

def _merge(env, platform_data=None, project_policy=None, allow=False):
    environment = {'data': {'auto_routing_policy': platform_data,
                            'auto_routing_policy_allow_project_activation': allow}}
    return env.settings.effective_policy(environment, {'data': {'policy': project_policy}})


def test_nothing_configured_is_defaults_from_default(env):
    policy, source = env.settings.effective_policy(None, None)
    assert policy == DEFAULTS
    assert set(source) == set(DEFAULTS) and set(source.values()) == {'default'}


def test_platform_policy_wins_over_defaults(env):
    stored = full(env, shadow_mode=False, selector={'artifact': 'sel-1', 'tau': 0.9}, fallback=GPT54)
    policy, source = _merge(env, stored)
    assert policy == stored
    assert set(source.values()) == {'platform'}


def test_project_overrides_per_top_level_key(env):
    stored = full(env, selector={'artifact': 'sel-1', 'tau': 0.9}, pool=['a', 'b'], fallback=GPT54)
    policy, source = _merge(env, stored, {'selector': {'tau': 0.7}, 'pool': ['c'], 'greeting_rule': False})
    assert policy['selector'] == {'artifact': 'sel-1', 'tau': 0.7}  # nested keys merge, siblings inherited
    assert policy['pool'] == ['c']  # arrays are replaced, not concatenated
    assert policy['greeting_rule'] is False and policy['fallback'] == GPT54
    assert source['selector'] == source['pool'] == source['greeting_rule'] == 'project'
    assert source['fallback'] == source['output'] == source['shadow_mode'] == 'platform'


def test_default_source_applies_below_a_project_override(env):
    policy, source = _merge(env, None, {'thinking_budget_tokens': {'high': 12000}})
    assert policy['thinking_budget_tokens'] == {'low': 2048, 'medium': 4096, 'high': 12000}
    assert source['thinking_budget_tokens'] == 'project' and source['selector'] == 'default'


def test_explicit_null_overrides_platform_value(env):
    stored = full(env, pool=['a'], fallback=GPT54, selector={'artifact': 'sel-1', 'tau': 0.9})
    policy, source = _merge(env, stored, {'pool': None, 'fallback': None, 'selector': {'artifact': None}})
    assert policy['pool'] is None and policy['fallback'] is None and policy['selector'] == {'artifact': None, 'tau': 0.9}
    assert source['pool'] == source['fallback'] == 'project'


def test_malformed_records_fail_closed_to_the_next_level(env):
    stored = full(env, selector={'artifact': 'sel-1', 'tau': 0.9})
    policy, _ = _merge(env, stored, {'selector': {'tau': 5}})
    assert policy['selector']['tau'] == 0.9
    policy, source = _merge(env, {'shadow_mode': 'no'}, None)
    assert policy == DEFAULTS and set(source.values()) == {'default'}
    assert env.settings.effective_policy('x', ['y'])[0] == DEFAULTS


def test_merge_does_not_mutate_inputs_or_share_state(env):
    stored = full(env)
    snapshot = copy.deepcopy(stored)
    first, _ = _merge(env, stored, {'output': {'max_cap': 100}})
    first['output']['max_cap'] = 1
    assert stored == snapshot and _merge(env, stored)[0] == snapshot


def test_project_cap_is_clamped_to_the_platform_cap_at_merge(env):
    stored = full(env, output={'default_allowance': 8000, 'min_cap': 256, 'max_cap': 20000})
    policy, _ = _merge(env, stored, {'output': {'max_cap': 32000}})
    assert policy['output']['max_cap'] == 20000
    policy, _ = _merge(env, stored, {'output': {'max_cap': 1000, 'min_cap': 500}})
    assert policy['output'] == {'default_allowance': 8000, 'min_cap': 500, 'max_cap': 1000}
    policy, _ = _merge(env, stored, {'output': {'min_cap': 30000}})
    assert policy['output']['min_cap'] == 20000  # never above the effective max_cap


def test_project_cannot_leave_shadow_without_permission(env):
    policy, source = _merge(env, full(env), {'shadow_mode': False})
    assert policy['shadow_mode'] is True and source['shadow_mode'] == 'platform'
    policy, source = _merge(env, None, {'shadow_mode': False})
    assert policy['shadow_mode'] is True and source['shadow_mode'] == 'default'
    policy, source = _merge(env, full(env), {'shadow_mode': False}, allow=True)
    assert policy['shadow_mode'] is False and source['shadow_mode'] == 'project'


def test_project_may_always_enter_shadow_and_never_escalates_a_live_platform(env):
    live = full(env, shadow_mode=False)
    assert _merge(env, live, {'shadow_mode': True})[0]['shadow_mode'] is True
    assert _merge(env, live, {'shadow_mode': False})[0]['shadow_mode'] is False


# --- effective_settings: revision, source, reasons -----------------------------------------

def _settings(env, platform_data=None, project_policy=None, items=None, allow=False):
    environment = {'data': {'auto_routing_available': True, 'auto_routing_project_default': True,
                            'auto_routing_classifier': LUNA, 'auto_routing_policy': platform_data,
                            'auto_routing_policy_allow_project_activation': allow}}
    return env.settings.effective_settings(environment, {'data': {'policy': project_policy}}, None, items)


def test_settings_return_policy_and_source(env):
    result = _settings(env, full(env, fallback=GPT54), {'greeting_rule': False})
    assert result['policy']['fallback'] == GPT54 and result['policy']['greeting_rule'] is False
    assert result['policy_source']['greeting_rule'] == 'project' and result['policy_source']['fallback'] == 'platform'


def test_revision_changes_with_each_effective_policy_change(env):
    revisions = {_settings(env)['revision'], _settings(env, full(env, shadow_mode=False))['revision'],
                 _settings(env, full(env, fallback=GPT54))['revision'],
                 _settings(env, None, {'selector': {'tau': 0.9}})['revision'],
                 _settings(env, None, {'pool': ['a']})['revision'],
                 _settings(env, None, {'output': {'max_cap': 100}})['revision']}
    assert len(revisions) == 6


def test_revision_ignores_messages_source_and_ineffective_overrides(env):
    items_a = [item('gpt-5.6-luna'), item('gpt-5.4', available=False)]
    items_b = [item('gpt-5.6-luna'), item('gpt-5.4', kind='image')]
    stored = full(env, fallback=GPT54)
    a, b = _settings(env, stored, items=items_a), _settings(env, stored, items=items_b)
    assert a['fallback_reason']['message'] != b['fallback_reason']['message']
    assert a['revision'] == b['revision']
    # same effective policy reached from another level: the source label is not part of revision
    assert _settings(env, stored)['revision'] == _settings(env, None, {'fallback': GPT54})['revision']
    # a refused activation leaves the effective policy, hence the revision, unchanged
    assert _settings(env, stored, {'shadow_mode': False})['revision'] == _settings(env, stored)['revision']


def test_revision_is_canonical_json_of_fields(env):
    result = _settings(env, full(env))
    fields = {key: result[key] for key in ('available', 'project_default', 'project_override', 'enabled', 'classifier', 'policy')}
    import hashlib
    assert result['revision'] == hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()


# --- fallback resolution and readiness -----------------------------------------------------

def _ready(env, fallback, inventory, platform_fallback=None):
    env.state.records = {PUBLIC: {'data': {'auto_routing_available': True, 'auto_routing_project_default': True,
                                           'auto_routing_classifier': LUNA,
                                           'auto_routing_policy': full(env, fallback=platform_fallback) if platform_fallback else None}},
                         7: {'data': {'policy': {'fallback': fallback} if fallback else None}}}
    env.state.inventory = {7: inventory}
    return env.readiness.get_auto_routing_readiness(7, 42)


def test_no_fallback_and_no_selector_leave_ready_unchanged(env):
    result = _ready(env, None, [item('gpt-5.6-luna')])
    assert result['ready'] is True and result['reasons'] == []


def test_resolvable_fallback_is_ready(env):
    assert _ready(env, GPT54, [item('gpt-5.6-luna'), item('gpt-5.4')])['ready'] is True


@pytest.mark.parametrize('inventory,message', [
    ([], 'Fallback model gpt-5.4 is no longer available to this project'),
    ([item('gpt-5.4', available=False)], 'Fallback model gpt-5.4 is no longer available to this project'),
    ([item('gpt-5.4', kind='image')], 'Fallback model gpt-5.4 is not a chat model'),
])
def test_unresolvable_fallback_reports_reason_and_is_not_ready(env, inventory, message):
    result = _ready(env, GPT54, [item('gpt-5.6-luna'), *inventory])
    assert result['ready'] is False
    assert result['reasons'] == [{'code': 'FALLBACK_UNAVAILABLE', 'model': GPT54, 'message': message}]


def test_platform_fallback_is_checked_too_and_inventory_read_for_it(env):
    result = _ready(env, None, [item('gpt-5.6-luna')], platform_fallback=GPT54)
    assert [r['code'] for r in result['reasons']] == ['FALLBACK_UNAVAILABLE']
    env.state.records = {PUBLIC: {'data': {'auto_routing_available': True, 'auto_routing_policy': full(env, fallback=GPT54)}},
                         7: {'data': {}}}
    env.state.inventory_calls.clear()
    env.rpc.configurations_get_auto_routing_settings(7, 42)
    assert env.state.inventory_calls == [(7, 42)]


def test_fallback_reason_is_independent_of_other_reasons(env):
    env.state.records = {PUBLIC: {'data': {'auto_routing_available': True, 'auto_routing_policy': full(env, fallback=GPT54)}},
                         7: {'data': {}}}
    env.state.inventory = {7: []}
    codes = [r['code'] for r in env.readiness.get_auto_routing_readiness(7, 42)['reasons']]
    assert codes == ['AUTO_DISABLED', 'CLASSIFIER_NOT_CONFIGURED', 'FALLBACK_UNAVAILABLE']


# --- RPC payload ---------------------------------------------------------------------------

def test_settings_rpc_payload_shape(env):
    env.state.records = {PUBLIC: {'data': {'auto_routing_available': True, 'auto_routing_classifier': LUNA,
                                           'auto_routing_policy': full(env, fallback=GPT54)}},
                         7: {'data': {'enabled': True, 'policy': {'selector': {'tau': 0.9}}}}}
    unchecked = env.rpc.configurations_get_auto_routing_settings(7)
    assert unchecked['policy']['selector']['tau'] == 0.9 and unchecked['fallback_reason'] is None
    assert env.state.inventory_calls == []
    env.state.inventory = {7: [item('gpt-5.6-luna')]}
    result = env.rpc.configurations_get_auto_routing_settings(7, 42)
    assert set(result['policy']) == set(DEFAULTS)
    assert result['policy_source']['selector'] == 'project' and result['policy_source']['fallback'] == 'platform'
    assert result['fallback_reason']['code'] == 'FALLBACK_UNAVAILABLE'
    assert json.loads(json.dumps(result)) == result


def test_absent_policy_leaves_legacy_fields_untouched(env):
    env.state.records = {PUBLIC: {'data': {'auto_routing_available': True}}, 7: {'data': {'enabled': True}}}
    result = env.rpc.configurations_get_auto_routing_settings(7, 42)
    assert result['policy'] == DEFAULTS and set(result['policy_source'].values()) == {'default'}
    assert result['enabled'] is True and result['classifier'] is None and result['fallback_reason'] is None


# --- project write validation --------------------------------------------------------------

def _project_write(env, data, old=None):
    return env.guard.validate_routing_write('auto_routing', 7, {'data': data},
                                            existing={'data': old or {}, 'elitea_title': 'auto_routing'})


def _set_platform(env, policy=None, allow=False):
    env.state.records = {PUBLIC: {'data': {'auto_routing_policy': policy, 'auto_routing_policy_allow_project_activation': allow}}}


def test_project_policy_is_stored_as_set_keys_only(env):
    _set_platform(env)
    result = _project_write(env, {'policy': {'selector': {'tau': 0.9}, 'pool': None}})
    assert result['data']['policy'] == {'selector': {'tau': 0.9}, 'pool': None}
    assert 'policy' not in _project_write(env, {'enabled': True})['data']
    assert 'policy' not in _project_write(env, {'policy': None}, {'policy': {'pool': ['a']}})['data']


def test_omitted_policy_is_preserved(env):
    _set_platform(env)
    old = {'policy': {'selector': {'tau': 0.9}}}
    assert _project_write(env, {'enabled': False}, old)['data']['policy'] == {'selector': {'tau': 0.9}}


def test_project_cap_cannot_exceed_platform_cap(env):
    _set_platform(env, full(env, output={'default_allowance': 8000, 'min_cap': 256, 'max_cap': 20000}))
    assert _project_write(env, {'policy': {'output': {'max_cap': 20000}}})['data']['policy']['output'] == {'max_cap': 20000}
    for output in ({'max_cap': 20001}, {'min_cap': 25000}):
        with pytest.raises(env.error) as caught:
            _project_write(env, {'policy': {'output': output}})
        assert caught.value.field == f"policy.output.{next(iter(output))}"


def test_project_cap_cannot_exceed_default_platform_cap(env):
    _set_platform(env)
    with pytest.raises(env.error, match='32000'):
        _project_write(env, {'policy': {'output': {'max_cap': 32001}}})


def test_project_cannot_leave_shadow_without_platform_permission(env):
    _set_platform(env)
    with pytest.raises(env.error) as caught:
        _project_write(env, {'policy': {'shadow_mode': False}})
    assert caught.value.field == 'policy.shadow_mode'
    assert _project_write(env, {'policy': {'shadow_mode': True}})['data']['policy'] == {'shadow_mode': True}
    _set_platform(env, allow=True)
    assert _project_write(env, {'policy': {'shadow_mode': False}})['data']['policy'] == {'shadow_mode': False}
    _set_platform(env, full(env, shadow_mode=False))
    assert _project_write(env, {'policy': {'shadow_mode': False}})['data']['policy'] == {'shadow_mode': False}


def test_unchanged_policy_is_never_revalidated(env):
    _set_platform(env, full(env, output={'default_allowance': 8000, 'min_cap': 256, 'max_cap': 10000}))
    env.state.inventory = {7: []}
    old = {'policy': {'output': {'max_cap': 32000}, 'shadow_mode': False, 'fallback': TEAM}}
    result = _project_write(env, {'enabled': False, 'policy': copy.deepcopy(old['policy'])}, old)
    assert result['data']['policy'] == old['policy']
    assert env.state.inventory_calls == []
    # an unrelated changed key does not re-check the stale ones either
    result = _project_write(env, {'policy': {**old['policy'], 'greeting_rule': False}}, old)
    assert result['data']['policy']['greeting_rule'] is False and env.state.inventory_calls == []


def test_project_fallback_must_be_visible_available_chat_when_changed(env):
    _set_platform(env)
    env.state.inventory = {7: [item('team-model', project_id=7, shared=False)]}
    assert _project_write(env, {'policy': {'fallback': TEAM}})['data']['policy'] == {'fallback': TEAM}
    assert env.state.inventory_calls == [(7, 42)]
    for inventory in ([], [item('team-model', project_id=7, available=False)],
                      [item('team-model', project_id=7, kind='embedding')], [item('team-model', project_id=8)]):
        env.state.inventory = {7: inventory}
        with pytest.raises(env.error) as caught:
            _project_write(env, {'policy': {'fallback': TEAM}})
        assert caught.value.field == 'policy.fallback'
        assert caught.value.message == 'Model team-model is not available to this project'


def test_project_fallback_null_and_unchanged_stale_are_accepted(env):
    _set_platform(env)
    env.state.inventory = {7: []}
    old = {'policy': {'fallback': TEAM}}
    assert _project_write(env, {'policy': {'fallback': None}}, old)['data']['policy'] == {'fallback': None}
    assert _project_write(env, {'policy': {'fallback': TEAM, 'greeting_rule': True}}, old)
    assert env.state.inventory_calls == []


def test_project_policy_still_requires_project_admin_and_strict_shape(env):
    _set_platform(env)
    env.state.project_admin = False
    with pytest.raises(env.error, match='project admins'):
        _project_write(env, {'policy': {'greeting_rule': False}})
    env.state.project_admin = True
    for bad in ({'selector': {'tau': 2}}, {'bogus': 1}, {'shadow_mode': None}):
        with pytest.raises(env.error) as caught:
            _project_write(env, {'policy': bad})
        assert caught.value.field == 'data'


# --- platform write validation -------------------------------------------------------------

def _platform_write(env, data, old=None, project_id=PUBLIC):
    return env.guard.validate_routing_write('environment_settings', project_id,
        {'data': data, 'elitea_title': 'environment_settings'},
        existing={'data': old or {}, 'elitea_title': 'environment_settings'})


def test_platform_accepts_policy_with_shared_fallback(env):
    env.state.inventory = {PUBLIC: [item('gpt-5.4')]}
    result = _platform_write(env, {'auto_routing_policy': {'fallback': GPT54, 'selector': {'tau': 0.9}},
                                   'auto_routing_policy_allow_project_activation': True})
    assert result['data']['auto_routing_policy'] == {**DEFAULTS, 'fallback': GPT54, 'selector': {'artifact': None, 'tau': 0.9}}
    assert result['data']['auto_routing_policy_allow_project_activation'] is True
    assert env.state.inventory_calls == [(PUBLIC, 42)]


@pytest.mark.parametrize('inventory', [[], [item('gpt-5.4', shared=False)], [item('gpt-5.4', kind='audio')],
                                       [item('gpt-5.4', available=False)]])
def test_platform_fallback_must_be_shared_available_chat(env, inventory):
    env.state.inventory = {PUBLIC: inventory}
    with pytest.raises(env.error) as caught:
        _platform_write(env, {'auto_routing_policy': {'fallback': GPT54}})
    assert caught.value.field == 'policy.fallback'


def test_platform_policy_and_activation_changes_require_administration_admin(env):
    env.state.inventory = {PUBLIC: [item('gpt-5.4')]}
    for data in ({'auto_routing_policy': {'greeting_rule': False}}, {'auto_routing_policy_allow_project_activation': True}):
        env.state.platform_admin = False
        with pytest.raises(env.error, match='Administration admins'):
            _platform_write(env, data)
        env.state.platform_admin = True
        with pytest.raises(env.error, match='Administration admins'):
            _platform_write(env, data, project_id=7)
        assert _platform_write(env, data)


def test_platform_policy_is_strict(env):
    for bad in ({'selector': {'tau': 0.2}}, {'output': {'max_cap': 200000}}, {'unknown': 1}, {'version': 2}):
        with pytest.raises(env.error) as caught:
            _platform_write(env, {'auto_routing_policy': bad})
        assert caught.value.field == 'data'


def test_platform_unchanged_policy_is_not_revalidated_and_legacy_save_preserves_it(env):
    env.state.inventory = {PUBLIC: []}
    stored = full(env, fallback=GPT54)
    old = {'auto_routing_policy': stored, 'auto_routing_policy_allow_project_activation': True}
    result = _platform_write(env, {'auto_routing_available': True, 'auto_routing_policy': copy.deepcopy(stored)}, old)
    assert result['data']['auto_routing_available'] is True and env.state.inventory_calls == []
    env.state.actor = None
    result = _platform_write(env, {'system_sender_name': 'Company'}, old)
    assert result['data']['auto_routing_policy'] == stored
    assert result['data']['auto_routing_policy_allow_project_activation'] is True
    env.state.actor = 42
    with pytest.raises(env.error, match='not available'):
        _platform_write(env, {'auto_routing_policy': {**stored, 'fallback': {'name': 'other', 'project_id': PUBLIC}}}, old)


def test_deleting_environment_with_policy_requires_admin(env):
    env.state.actor = None
    with pytest.raises(env.error, match='Administration admins'):
        env.guard.validate_routing_write('environment_settings', PUBLIC, {}, deleting=True, existing={
            'data': {'auto_routing_policy': full(env)}, 'elitea_title': 'environment_settings'})


def test_platform_policy_requires_environment_settings_record(env):
    with pytest.raises(env.error, match='environment_settings record'):
        env.guard.validate_routing_write('environment_settings', PUBLIC,
            {'data': {'auto_routing_policy': {}}, 'elitea_title': 'other'}, existing={'data': {}, 'elitea_title': 'other'})


# --- Administration method -----------------------------------------------------------------

@pytest.fixture
def admin(env, monkeypatch):
    writes = []
    env.state.records = {PUBLIC: {'id': 9, 'data': {'system_sender_name': 'Company'}}}

    def update(project_id, config_id, payload):
        writes.append((project_id, config_id, payload))
        return {'id': config_id, **payload}
    monkeypatch.setattr(env.method, 'update_configuration', update)
    return types.SimpleNamespace(method=env.method.Method(), writes=writes)


def test_method_returns_effective_platform_policy_and_activation(env, admin):
    assert admin.method.auto_routing_platform_settings()['policy'] == DEFAULTS
    assert admin.method.auto_routing_platform_settings()['allow_project_activation'] is False
    env.state.records[PUBLIC]['data'].update(auto_routing_policy=full(env, shadow_mode=False),
                                             auto_routing_policy_allow_project_activation=True)
    result = admin.method.auto_routing_platform_settings()
    assert result['policy']['shadow_mode'] is False and result['allow_project_activation'] is True


def test_method_put_accepts_policy_and_activation(env, admin):
    result = admin.method.auto_routing_platform_settings({
        'available': True, 'project_default': False, 'policy': {'greeting_rule': False}, 'allow_project_activation': True})
    data = admin.writes[0][2]['data']
    assert data['auto_routing_policy'] == {'greeting_rule': False} and data['auto_routing_policy_allow_project_activation'] is True
    assert result['allow_project_activation'] is True


def test_method_put_without_policy_keeps_stored_and_null_clears(env, admin):
    env.state.records[PUBLIC]['data']['auto_routing_policy'] = full(env, fallback=GPT54)
    admin.method.auto_routing_platform_settings({'available': True, 'project_default': True})
    assert admin.writes[0][2]['data']['auto_routing_policy'] == full(env, fallback=GPT54)
    admin.method.auto_routing_platform_settings({'available': True, 'project_default': True, 'policy': None})
    assert admin.writes[1][2]['data']['auto_routing_policy'] is None


@pytest.mark.parametrize('values', [
    {'available': True, 'project_default': False, 'allow_project_activation': 'yes'},
    {'available': True, 'project_default': False, 'allow_project_activation': 1},
    {'available': True, 'project_default': False, 'policy_x': {}},
    {'available': True, 'policy': {}},
])
def test_method_put_keeps_the_strict_key_check(env, admin, values):
    with pytest.raises(ValueError):
        admin.method.auto_routing_platform_settings(values)
    assert admin.writes == []
