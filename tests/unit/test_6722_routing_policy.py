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
    'version': 1, 'shadow_mode': True, 'selector': {'artifact': None, 'tau': 0.85, 'abstain': True}, 'pool': None, 'fallback': None,
    'output': {'default_allowance': 8000, 'min_cap': 256, 'max_cap': 32000},
    'thinking_budget_tokens': {'low': 2048, 'medium': 4096, 'high': 9092},
    'greeting_rule': True, 'classifier': {'enabled': False},
}
ART = {'artifact': 'sel-1', 'tau': 0.85, 'abstain': True}
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
             with_(['selector', 'extra'], 1), with_(['selector', 'abstain'], 'yes'), with_(['output', 'extra'], 1), with_(['classifier', 'extra'], 1),
             with_(['thinking_budget_tokens', 'extra'], 1), with_(['fallback'], {'name': 'm', 'project_id': 1, 'x': 1}),
             with_(['fallback'], 'gpt-5.4'), with_(['selector', 'artifact'], ''), with_(['selector', 'artifact'], '../x'),
             with_(['selector', 'artifact'], 'a' * 129)]
    cases += [with_(['selector', 'tau'], v) for v in (0.49, 1.0, 0.0, True, '0.9', None)]
    cases += [with_(['output', 'default_allowance'], v) for v in (255, 128001, True, 8000.5, None)]
    cases += [with_(['output', 'min_cap'], v) for v in (0, 1, 255, 128001)]
    cases += [with_(['output', 'max_cap'], v) for v in (0, 255, 128001, '32000')]
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
    (('output', 'min_cap'), 256), (('output', 'max_cap'), 128000), (('selector', 'abstain'), False), (('thinking_budget_tokens', 'low'), 0),
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
    stored = full(env, shadow_mode=False, selector={'artifact': 'sel-1', 'tau': 0.9, 'abstain': True}, fallback=GPT54)
    policy, source = _merge(env, stored)
    assert policy == stored
    assert set(source.values()) == {'platform'}


def test_project_overrides_per_top_level_key(env):
    stored = full(env, selector={'artifact': 'sel-1', 'tau': 0.9, 'abstain': True}, pool=['a', 'b'], fallback=GPT54)
    policy, source = _merge(env, stored, {'selector': {'tau': 0.7}, 'pool': ['b'], 'greeting_rule': False})
    assert policy['selector'] == {'artifact': 'sel-1', 'tau': 0.7, 'abstain': True}  # nested keys merge, siblings inherited
    assert policy['pool'] == ['b']  # arrays are replaced, not concatenated
    assert policy['greeting_rule'] is False and policy['fallback'] == GPT54
    assert source['selector'] == source['pool'] == source['greeting_rule'] == 'project'
    assert source['fallback'] == source['output'] == source['shadow_mode'] == 'platform'


def test_default_source_applies_below_a_project_override(env):
    policy, source = _merge(env, None, {'thinking_budget_tokens': {'high': 12000}})
    assert policy['thinking_budget_tokens'] == {'low': 2048, 'medium': 4096, 'high': 12000}
    assert source['thinking_budget_tokens'] == 'project' and source['selector'] == 'default'


def test_explicit_null_clears_only_what_the_platform_does_not_restrict(env):
    stored = full(env, selector={'artifact': 'sel-1', 'tau': 0.9, 'abstain': True})
    policy, source = _merge(env, stored, {'pool': None, 'fallback': None, 'selector': {'artifact': None}})
    assert policy['pool'] is None and policy['fallback'] is None and policy['selector']['artifact'] is None
    assert source['pool'] == source['fallback'] == 'project'


def test_project_cannot_lift_platform_pool_or_fallback_at_merge(env):
    stored = full(env, pool=['a', 'b'], fallback=GPT54)
    policy, source = _merge(env, stored, {'pool': None, 'fallback': None})
    assert policy['pool'] == ['a', 'b'] and policy['fallback'] == GPT54
    assert source['pool'] == source['fallback'] == 'platform'


def test_project_pool_may_narrow_but_not_widen_at_merge(env):
    stored = full(env, pool=['a', 'b'])
    assert _merge(env, stored, {'pool': ['b']})[0]['pool'] == ['b']
    assert _merge(env, stored, {'pool': ['b']})[1]['pool'] == 'project'
    for widened in (['a', 'c'], ['c']):
        policy, source = _merge(env, stored, {'pool': widened})
        assert policy['pool'] == ['a', 'b'] and source['pool'] == 'platform'
    assert _merge(env, full(env), {'pool': ['x', 'y']})[0]['pool'] == ['x', 'y']  # no platform pool: any list
    assert _merge(env, stored, {'fallback': TEAM})[0]['fallback'] == TEAM  # a project may set its own fallback


def test_malformed_records_fail_closed_to_the_next_level(env):
    stored = full(env, selector={'artifact': 'sel-1', 'tau': 0.9, 'abstain': True})
    policy, _ = _merge(env, stored, {'selector': {'tau': 5}})
    assert policy['selector']['tau'] == 0.9
    policy, source = _merge(env, {'shadow_mode': 'no'}, None)
    assert policy == DEFAULTS and set(source.values()) == {'default'}
    assert env.settings.effective_policy('x', ['y'])[0] == DEFAULTS


def test_merge_does_not_mutate_inputs_or_share_state(env):
    stored = full(env)
    snapshot = copy.deepcopy(stored)
    first, _ = _merge(env, stored, {'output': {'max_cap': 300}})
    first['output']['max_cap'] = 1
    assert stored == snapshot and _merge(env, stored)[0] == snapshot


def test_project_cap_is_clamped_to_the_platform_cap_at_merge(env):
    stored = full(env, output={'default_allowance': 8000, 'min_cap': 256, 'max_cap': 20000})
    policy, _ = _merge(env, stored, {'output': {'max_cap': 32000}})
    assert policy['output']['max_cap'] == 20000
    policy, _ = _merge(env, stored, {'output': {'max_cap': 1000, 'min_cap': 500}})
    assert policy['output'] == {'default_allowance': 1000, 'min_cap': 500, 'max_cap': 1000}  # allowance follows the caps
    policy, _ = _merge(env, stored, {'output': {'min_cap': 30000}})
    assert policy['output']['min_cap'] == 20000  # never above the effective max_cap


def test_project_cannot_leave_shadow_without_permission(env):
    policy, source = _merge(env, full(env, selector=ART), {'shadow_mode': False})
    assert policy['shadow_mode'] is True and source['shadow_mode'] == 'platform'
    policy, source = _merge(env, None, {'shadow_mode': False, 'selector': {'artifact': 'sel-1'}})
    assert policy['shadow_mode'] is True and source['shadow_mode'] == 'default'
    policy, source = _merge(env, full(env, selector=ART), {'shadow_mode': False}, allow=True)
    assert policy['shadow_mode'] is False and source['shadow_mode'] == 'project'


def test_project_may_always_enter_shadow_and_never_escalates_a_live_platform(env):
    live = full(env, shadow_mode=False, selector=ART)
    assert _merge(env, live, {'shadow_mode': True})[0]['shadow_mode'] is True
    assert _merge(env, live, {'shadow_mode': False})[0]['shadow_mode'] is False


# --- effective_settings: revision, source, reasons -----------------------------------------

def _settings(env, platform_data=None, project_policy=None, items=None, allow=False):
    environment = {'data': {'auto_routing_available': True, 'auto_routing_policy_enabled': True, 'auto_routing_project_default': True,
                            'auto_routing_classifier': LUNA, 'auto_routing_policy': platform_data,
                            'auto_routing_policy_allow_project_activation': allow}}
    return env.settings.effective_settings(environment, {'data': {'policy': project_policy}}, None, items)


def test_settings_return_policy_and_source(env):
    result = _settings(env, full(env, fallback=GPT54), {'greeting_rule': False})
    assert result['policy']['fallback'] == GPT54 and result['policy']['greeting_rule'] is False
    assert result['policy_source']['greeting_rule'] == 'project' and result['policy_source']['fallback'] == 'platform'


def test_revision_changes_with_each_effective_policy_change(env):
    revisions = {_settings(env)['revision'], _settings(env, full(env, shadow_mode=False, selector=ART))['revision'],
                 _settings(env, full(env, fallback=GPT54))['revision'],
                 _settings(env, None, {'selector': {'tau': 0.9}})['revision'],
                 _settings(env, None, {'pool': ['a']})['revision'],
                 _settings(env, None, {'output': {'max_cap': 300}})['revision']}
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
    assert _settings(env, full(env, fallback=GPT54, selector=ART), {'shadow_mode': False})['revision'] == _settings(env, full(env, fallback=GPT54, selector=ART))['revision']


def test_revision_is_canonical_json_of_fields(env):
    result = _settings(env, full(env))
    fields = {key: result[key] for key in ('available', 'project_default', 'project_override', 'enabled', 'classifier', 'policy')}
    import hashlib
    assert result['revision'] == hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()


# --- fallback resolution and readiness -----------------------------------------------------

def _ready(env, fallback, inventory, platform_fallback=None):
    env.state.records = {PUBLIC: {'data': {'auto_routing_available': True, 'auto_routing_policy_enabled': True, 'auto_routing_project_default': True,
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
    env.state.records = {PUBLIC: {'data': {'auto_routing_available': True, 'auto_routing_policy_enabled': True, 'auto_routing_policy': full(env, fallback=GPT54)}},
                         7: {'data': {}}}
    env.state.inventory_calls.clear()
    env.rpc.configurations_get_auto_routing_settings(7, 42)
    assert env.state.inventory_calls == [(7, 42)]


def test_fallback_reason_is_independent_of_other_reasons(env):
    env.state.records = {PUBLIC: {'data': {'auto_routing_available': True, 'auto_routing_policy_enabled': True, 'auto_routing_policy': full(env, fallback=GPT54)}},
                         7: {'data': {}}}
    env.state.inventory = {7: []}
    codes = [r['code'] for r in env.readiness.get_auto_routing_readiness(7, 42)['reasons']]
    assert codes == ['AUTO_DISABLED', 'CLASSIFIER_NOT_CONFIGURED', 'FALLBACK_UNAVAILABLE']


# --- RPC payload ---------------------------------------------------------------------------

def test_settings_rpc_payload_shape(env):
    env.state.records = {PUBLIC: {'data': {'auto_routing_available': True, 'auto_routing_policy_enabled': True, 'auto_routing_classifier': LUNA,
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
    env.state.records = {PUBLIC: {'data': {'auto_routing_available': True, 'auto_routing_policy_enabled': True}}, 7: {'data': {'enabled': True}}}
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
    _set_platform(env, full(env, selector=ART))
    with pytest.raises(env.error) as caught:
        _project_write(env, {'policy': {'shadow_mode': False}})
    assert caught.value.field == 'policy.shadow_mode'
    assert _project_write(env, {'policy': {'shadow_mode': True}})['data']['policy'] == {'shadow_mode': True}
    _set_platform(env, full(env, selector=ART), allow=True)
    assert _project_write(env, {'policy': {'shadow_mode': False}})['data']['policy'] == {'shadow_mode': False}
    _set_platform(env, full(env, shadow_mode=False, selector=ART))
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
    assert result['data']['auto_routing_policy'] == {**DEFAULTS, 'fallback': GPT54,
                                                      'selector': {'artifact': None, 'tau': 0.9, 'abstain': True}}
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
    result = _platform_write(env, {'auto_routing_available': True, 'auto_routing_policy_enabled': True, 'auto_routing_policy': copy.deepcopy(stored)}, old)
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
    env.state.records[PUBLIC]['data'].update(auto_routing_policy=full(env, shadow_mode=False, selector=ART),
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


# --- review round: contract rules shared with the Gateway ----------------------------------

def _budgets(low, medium, high):
    return {'low': low, 'medium': medium, 'high': high}


@pytest.mark.parametrize('budgets', [_budgets(2048, 2048, 9092), _budgets(4096, 2048, 9092), _budgets(0, 0, 0),
                                     _budgets(2048, 4096, 4096), _budgets(9092, 4096, 2048)])
def test_budgets_must_strictly_increase_on_the_full_model(env, budgets):
    with pytest.raises(ValidationError, match='low < medium < high'):
        env.pd.AutoRoutingPolicy.model_validate({**DEFAULTS, 'thinking_budget_tokens': budgets})
    with pytest.raises(ValidationError):
        env.environment(auto_routing_policy={**DEFAULTS, 'thinking_budget_tokens': budgets})


def test_partial_budget_override_is_schema_valid_but_checked_after_merge(env):
    assert env.pd.AutoRoutingPolicyOverride.model_validate({'thinking_budget_tokens': {'low': 5000}})
    _set_platform(env)
    with pytest.raises(env.error) as caught:
        _project_write(env, {'policy': {'thinking_budget_tokens': {'low': 5000}}})  # 5000 !< platform medium 4096
    assert caught.value.field == 'policy.thinking_budget_tokens'
    ok = _project_write(env, {'policy': {'thinking_budget_tokens': {'low': 1000, 'high': 20000}}})
    assert ok['data']['policy']['thinking_budget_tokens'] == {'low': 1000, 'high': 20000}


def test_out_of_order_stored_budgets_fall_back_to_the_platform_at_merge(env):
    stored = full(env, thinking_budget_tokens=_budgets(1000, 2000, 3000))
    policy, source = _merge(env, stored, {'thinking_budget_tokens': {'low': 5000}})
    assert policy['thinking_budget_tokens'] == _budgets(1000, 2000, 3000) and source['thinking_budget_tokens'] == 'platform'
    policy, source = _merge(env, None, {'thinking_budget_tokens': {'medium': 2048}})  # equal to low
    assert policy['thinking_budget_tokens'] == _budgets(2048, 4096, 9092) and source['thinking_budget_tokens'] == 'default'


def test_stale_out_of_order_budgets_do_not_block_unrelated_saves(env):
    _set_platform(env)
    old = {'policy': {'thinking_budget_tokens': {'low': 5000}}}
    assert _project_write(env, {'policy': {**old['policy'], 'greeting_rule': False}}, old)


def test_default_allowance_is_clamped_into_the_effective_caps(env):
    stored = full(env, output={'default_allowance': 8000, 'min_cap': 256, 'max_cap': 32000})
    policy, _ = _merge(env, stored, {'output': {'max_cap': 4000}})  # lowered project cap
    assert policy['output'] == {'default_allowance': 4000, 'min_cap': 256, 'max_cap': 4000}
    low = full(env, output={'default_allowance': 300, 'min_cap': 256, 'max_cap': 32000})
    assert _merge(env, low, {'output': {'min_cap': 500}})[0]['output']['default_allowance'] == 500
    assert _merge(env, stored)[0]['output']['default_allowance'] == 8000
    inherited = full(env, output={'default_allowance': 50000, 'min_cap': 256, 'max_cap': 32000})
    assert _merge(env, inherited)[0]['output']['default_allowance'] == 32000


def test_project_default_allowance_cannot_exceed_platform_cap(env):
    _set_platform(env, full(env, output={'default_allowance': 8000, 'min_cap': 256, 'max_cap': 20000}))
    assert _project_write(env, {'policy': {'output': {'default_allowance': 20000}}})
    with pytest.raises(env.error) as caught:
        _project_write(env, {'policy': {'output': {'default_allowance': 20001}}})
    assert caught.value.field == 'policy.output.default_allowance'


def test_abstain_defaults_on_and_merges(env):
    assert env.pd.AutoRoutingPolicy().selector.abstain is True
    policy, source = _merge(env, full(env, selector=ART), {'selector': {'abstain': False}})
    assert policy['selector'] == {**ART, 'abstain': False} and source['selector'] == 'project'
    assert env.pd.AutoRoutingPolicyOverride.model_validate({'selector': {'abstain': False}}).model_dump(
        exclude_unset=True) == {'selector': {'abstain': False}}


def test_leaving_shadow_requires_an_artifact_on_platform_writes(env):
    env.state.inventory = {PUBLIC: []}
    with pytest.raises(env.error) as caught:
        _platform_write(env, {'auto_routing_policy': {'shadow_mode': False}})
    assert caught.value.field == 'policy.selector.artifact'
    assert caught.value.message == 'Leaving shadow mode requires a pinned selector artifact'
    assert _platform_write(env, {'auto_routing_policy': {'shadow_mode': False, 'selector': {'artifact': 'sel-1'}}})
    assert _platform_write(env, {'auto_routing_policy': {'shadow_mode': True}})


def test_leaving_shadow_requires_an_artifact_on_project_writes(env):
    _set_platform(env, allow=True)  # allowed to activate, but the platform pins no artifact
    with pytest.raises(env.error) as caught:
        _project_write(env, {'policy': {'shadow_mode': False}})
    assert caught.value.field == 'policy.selector.artifact'
    assert _project_write(env, {'policy': {'shadow_mode': False, 'selector': {'artifact': 'sel-2'}}})
    _set_platform(env, full(env, selector=ART), allow=True)  # artifact inherited from the platform
    assert _project_write(env, {'policy': {'shadow_mode': False}})
    with pytest.raises(env.error, match='pinned selector artifact'):  # explicit null removes the inherited pin
        _project_write(env, {'policy': {'shadow_mode': False, 'selector': {'artifact': None}}})
    _set_platform(env, full(env, shadow_mode=False, selector=ART))  # live platform: dropping the pin is refused
    with pytest.raises(env.error, match='pinned selector artifact'):
        _project_write(env, {'policy': {'selector': {'artifact': None}}})


def test_activation_without_artifact_is_forced_to_shadow_and_advisory(env):
    stored = full(env, shadow_mode=False)  # stored before the guard, or by a direct database edit
    result = _settings(env, stored)
    assert result['policy']['shadow_mode'] is True and result['policy_issues'] == [
        {'code': 'ACTIVATION_WITHOUT_ARTIFACT', 'source': 'policy'}]
    ready = _ready_with(env, stored)
    assert ready['ready'] is True
    assert ready['reasons'] == [{'code': 'ACTIVATION_WITHOUT_ARTIFACT', 'message':
        'The Auto routing policy leaves shadow mode without a pinned selector artifact; shadow mode stays on'}]
    assert _settings(env, full(env, shadow_mode=False, selector=ART))['policy']['shadow_mode'] is False


def _ready_with(env, platform_policy_value, project_policy=None):
    env.state.records = {PUBLIC: {'data': {'auto_routing_available': True, 'auto_routing_policy_enabled': True, 'auto_routing_project_default': True,
                                           'auto_routing_classifier': LUNA, 'auto_routing_policy': platform_policy_value}},
                         7: {'data': {'policy': project_policy}}}
    env.state.inventory = {7: [item('gpt-5.6-luna')]}
    return env.readiness.get_auto_routing_readiness(7, 42)


def test_malformed_stored_platform_policy_serves_defaults_and_is_advisory(env):
    result = _settings(env, {'shadow_mode': 'no'})
    assert result['policy'] == DEFAULTS and result['policy_issues'] == [{'code': 'POLICY_INVALID_STORED', 'source': 'platform'}]
    ready = _ready_with(env, {'selector': {'tau': 9}})
    assert ready['ready'] is True and [r['code'] for r in ready['reasons']] == ['POLICY_INVALID_STORED']
    assert 'built-in defaults' in ready['reasons'][0]['message']
    assert _ready_with(env, None)['reasons'] == []
    project = _ready_with(env, None, {'selector': {'tau': 9}})
    assert project['ready'] is True and 'ignored' in project['reasons'][0]['message']


def test_advisory_reasons_do_not_hide_blocking_ones(env):
    env.state.records = {PUBLIC: {'data': {'auto_routing_available': True, 'auto_routing_policy_enabled': True, 'auto_routing_policy': {'x': 1}}}, 7: {'data': {}}}
    env.state.inventory = {7: []}
    result = env.readiness.get_auto_routing_readiness(7, 42)
    assert result['ready'] is False
    assert [r['code'] for r in result['reasons']] == ['AUTO_DISABLED', 'CLASSIFIER_NOT_CONFIGURED', 'POLICY_INVALID_STORED']


def test_admin_payload_flags_an_invalid_stored_policy(env, admin):
    assert admin.method.auto_routing_platform_settings()['policy_stored_invalid'] is False
    env.state.records[PUBLIC]['data']['auto_routing_policy'] = {'shadow_mode': 'no'}
    result = admin.method.auto_routing_platform_settings()
    assert result['policy_stored_invalid'] is True and result['policy'] == DEFAULTS


def test_models_listing_block_carries_policy_and_source(env):
    env.state.records = {PUBLIC: {'data': {'auto_routing_available': True, 'auto_routing_policy_enabled': True, 'auto_routing_classifier': LUNA,
                                           'auto_routing_policy': full(env, selector=ART)}},
                         7: {'data': {'enabled': True, 'policy': {'greeting_rule': False}}}}
    env.state.inventory = {7: [item('gpt-5.6-luna')]}
    gates, refs, items = env.settings.get_classifier_state(7, 42, check_availability=True)
    block = env.readiness.models_auto_routing(gates, refs, items)
    assert set(block) == {'enabled', 'revision', 'profile_ref', 'policy', 'policy_source', 'readiness'}
    assert block['policy']['greeting_rule'] is False and block['policy']['selector'] == ART
    assert block['policy_source']['greeting_rule'] == 'project' and block['policy_source']['selector'] == 'platform'
    assert block['revision'] == gates['revision'] and block['readiness']['ready'] is True


def test_models_endpoint_uses_the_block_helper():
    source = (ROOT / 'api/v2/models.py').read_text()
    assert "result['auto_routing'] = models_auto_routing(gates, refs, items)" in source


# Cross-component merge table (the Gateway fixture was not published on feat/6722-v5-selector): each row is
# (platform policy, allow activation, project override) -> expected effective values and write outcome.
MERGE_CASES = [
    ('lowered project max_cap clamps the platform allowance',
     {'selector': ART}, False, {'output': {'max_cap': 4000}},
     {'output': {'default_allowance': 4000, 'min_cap': 256, 'max_cap': 4000}}, None),
    ('equal budgets: refused on write, platform budgets on merge',
     {}, False, {'thinking_budget_tokens': {'medium': 2048}},
     {'thinking_budget_tokens': _budgets(2048, 4096, 9092)}, 'policy.thinking_budget_tokens'),
    ('min_cap 1 is below the floor and the whole override is rejected',
     {}, False, {'output': {'min_cap': 1}}, {'output': {'default_allowance': 8000, 'min_cap': 256, 'max_cap': 32000}}, 'data'),
    ('shadow false without an artifact: refused on write, forced shadow on merge',
     {}, True, {'shadow_mode': False}, {'shadow_mode': True}, 'policy.selector.artifact'),
    ('shadow false with a platform artifact and permission is accepted',
     {'selector': ART}, True, {'shadow_mode': False}, {'shadow_mode': False}, None),
    ('shadow false without permission: refused on write, ignored on merge',
     {'selector': ART}, False, {'shadow_mode': False}, {'shadow_mode': True}, 'policy.shadow_mode'),
]


@pytest.mark.parametrize('name,platform_overrides,allow,override,expected,refused', MERGE_CASES, ids=[c[0] for c in MERGE_CASES])
def test_merge_case_table(env, name, platform_overrides, allow, override, expected, refused):
    stored = full(env, **platform_overrides)
    policy, _ = _merge(env, stored, override, allow=allow)
    assert {key: policy[key] for key in expected} == expected
    _set_platform(env, stored, allow)
    env.state.inventory = {7: []}
    if refused:
        with pytest.raises(env.error) as caught:
            _project_write(env, {'policy': override})
        assert caught.value.field == refused
    else:
        assert _project_write(env, {'policy': override})['data']['policy'] == override


# --- review round 2: pool/fallback narrowing, pool existence, platform switch ---------------

def _inventory(env, *models):
    env.state.inventory = {7: list(models), PUBLIC: list(models)}


def canonical_item(name, canonical, **kw):
    return {**item(name, **kw), 'identity': {'kind': 'chat', 'canonical': canonical, 'low_tier_hint': False}}


def test_project_write_cannot_lift_or_widen_the_platform_pool(env):
    _set_platform(env, full(env, pool=['gpt-5.4', 'openai/gpt-5-6']))
    _inventory(env, item('gpt-5.4'), canonical_item('luna', 'openai/gpt-5-6'), item('other'))
    assert _project_write(env, {'policy': {'pool': ['gpt-5.4']}})['data']['policy'] == {'pool': ['gpt-5.4']}
    for bad in (None, ['gpt-5.4', 'other']):
        with pytest.raises(env.error) as caught:
            _project_write(env, {'policy': {'pool': bad}})
        assert caught.value.field == 'policy.pool'
    # no platform pool: any list of visible models, and null is a no-op
    _set_platform(env)
    assert _project_write(env, {'policy': {'pool': ['other']}})
    assert _project_write(env, {'policy': {'pool': None}})


def test_project_write_cannot_remove_the_platform_fallback(env):
    _set_platform(env, full(env, fallback=GPT54))
    _inventory(env, item('gpt-5.4'), item('team-model', project_id=7))
    with pytest.raises(env.error) as caught:
        _project_write(env, {'policy': {'fallback': None}})
    assert caught.value.field == 'policy.fallback'
    assert _project_write(env, {'policy': {'fallback': TEAM}})['data']['policy'] == {'fallback': TEAM}
    # an unchanged stored null is not re-checked
    assert _project_write(env, {'policy': {'fallback': None, 'greeting_rule': False}}, {'policy': {'fallback': None}})


def test_pool_entries_must_be_visible_canonical_ids_or_exact_names(env):
    _set_platform(env)
    _inventory(env, canonical_item('luna', 'openai/gpt-5-6'), item('plain'))
    assert _project_write(env, {'policy': {'pool': ['openai/gpt-5-6', 'plain', 'luna']}})
    with pytest.raises(env.error) as caught:
        _project_write(env, {'policy': {'pool': ['openai/gpt-5-6', 'anthropic/claude-x', 'ghost', 'ghost']}})
    assert caught.value.field == 'policy.pool'
    assert caught.value.message == 'Unknown model(s): anthropic/claude-x, ghost'
    with pytest.raises(env.error, match='Unknown model'):
        _project_write(env, {'policy': {'pool': ['PLAIN']}})


def test_unchanged_pool_is_not_revalidated(env):
    _set_platform(env)
    _inventory(env)
    old = {'policy': {'pool': ['ghost']}}
    assert _project_write(env, {'policy': {'pool': ['ghost'], 'greeting_rule': False}}, old)
    assert env.state.inventory_calls == []


def test_platform_pool_entries_must_be_shared_visible_models(env):
    env.state.inventory = {PUBLIC: [canonical_item('luna', 'openai/gpt-5-6'), item('private', shared=False)]}
    assert _platform_write(env, {'auto_routing_policy': {'pool': ['openai/gpt-5-6', 'luna']}})
    for pool in (['private'], ['openai/other']):
        with pytest.raises(env.error) as caught:
            _platform_write(env, {'auto_routing_policy': {'pool': pool}})
        assert caught.value.field == 'policy.pool'
    env.state.inventory_calls.clear()
    old = {'auto_routing_policy': full(env, pool=['ghost'])}
    assert _platform_write(env, {'auto_routing_policy': full(env, pool=['ghost']), 'auto_routing_available': True}, old)
    assert env.state.inventory_calls == []


def test_tau_description_documents_the_low_floor(env):
    description = env.pd.SelectorPolicy.model_fields['tau'].description
    assert '0.7' in description and 'cheapest' in description


# platform switch: off (default) = the contract and revision of main

def _gates(env, enabled, policy=None, project=None):
    environment = {'data': {'auto_routing_available': True, 'auto_routing_project_default': True,
                            'auto_routing_classifier': LUNA, 'auto_routing_policy': policy,
                            'auto_routing_policy_enabled': enabled}}
    return env.settings.effective_settings(environment, {'data': {'policy': project}}, None, [item('gpt-5.6-luna')])


def test_policy_is_not_served_while_the_platform_switch_is_off(env):
    assert env.environment().auto_routing_policy_enabled is False
    stored = full(env, fallback=GPT54, selector=ART, shadow_mode=False)
    for enabled in (False, None):
        gates = _gates(env, enabled, stored, {'greeting_rule': False})
        for key in ('policy', 'policy_source', 'policy_issues', 'fallback_reason'):
            assert key not in gates
    assert 'policy' not in _gates(env, False)


def test_revision_is_independent_of_policy_while_off_and_rotates_once_on_enable(env):
    off = {_gates(env, False)['revision'], _gates(env, False, full(env, fallback=GPT54))['revision'],
           _gates(env, False, None, {'greeting_rule': False})['revision']}
    assert len(off) == 1
    assert _gates(env, True)['revision'] not in off
    assert _gates(env, True)['revision'] != _gates(env, True, full(env, fallback=GPT54))['revision']


def test_switch_off_matches_the_pre_policy_shape(env):
    env.state.records = {PUBLIC: {'data': {'auto_routing_available': True, 'auto_routing_classifier': LUNA,
                                           'auto_routing_policy': full(env, fallback=GPT54)}},
                         7: {'data': {'enabled': True}}}
    env.state.inventory = {7: [item('gpt-5.6-luna')]}
    result = env.rpc.configurations_get_auto_routing_settings(7, 42)
    assert set(result) == {'available', 'project_default', 'project_override', 'enabled', 'classifier', 'revision',
                           'classifier_reason'}
    assert env.state.inventory_calls == [(7, 42)]  # classifier only; no extra read for an unserved fallback
    ready = env.readiness.get_auto_routing_readiness(7, 42)
    assert ready['ready'] is True and ready['reasons'] == []  # an unusable fallback is irrelevant while off


def test_models_block_omits_policy_while_off_and_carries_it_when_on(env):
    for enabled, expected in ((False, False), (True, True)):
        env.state.records = {PUBLIC: {'data': {'auto_routing_available': True, 'auto_routing_classifier': LUNA,
                                               'auto_routing_policy_enabled': enabled}}, 7: {'data': {'enabled': True}}}
        env.state.inventory = {7: [item('gpt-5.6-luna')]}
        gates, refs, items = env.settings.get_classifier_state(7, 42, check_availability=True)
        block = env.readiness.models_auto_routing(gates, refs, items)
        assert ('policy' in block and 'policy_source' in block) is expected


def test_switch_change_requires_administration_admin(env):
    env.state.platform_admin = False
    with pytest.raises(env.error, match='Administration admins'):
        _platform_write(env, {'auto_routing_policy_enabled': True})
    env.state.platform_admin = True
    assert _platform_write(env, {'auto_routing_policy_enabled': True})['data']['auto_routing_policy_enabled'] is True
    env.state.actor = None  # a legacy save keeps it
    old = {'auto_routing_policy_enabled': True}
    assert _platform_write(env, {'system_sender_name': 'Company'}, old)['data']['auto_routing_policy_enabled'] is True


def test_method_exposes_and_sets_the_switch(env, admin):
    assert admin.method.auto_routing_platform_settings()['policy_enabled'] is False
    result = admin.method.auto_routing_platform_settings({'available': True, 'project_default': True, 'policy_enabled': True})
    assert admin.writes[0][2]['data']['auto_routing_policy_enabled'] is True and result['policy_enabled'] is True
    with pytest.raises(ValueError):
        admin.method.auto_routing_platform_settings({'available': True, 'project_default': True, 'policy_enabled': 'yes'})
