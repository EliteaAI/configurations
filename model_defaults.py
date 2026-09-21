"""Project creation defaults; concrete internal/pipeline defaults remain intact."""
AUTO_DEFAULT_KEY = 'default_llm_selection_mode'


def effective_default_selection(secrets, gates):
    if secrets.get(AUTO_DEFAULT_KEY) == 'auto' and gates.get('enabled') is True:
        return {'mode': 'auto', 'profile_ref': {'id': 'v7-quality-cost', 'revision': 1},
                'scope_mode': 'task_episode', 'reasoning': {'mode': 'auto'}}
    return None


def update_default_secrets(secrets, parsed, gates, concrete_default=None):
    result = dict(secrets)
    if parsed.mode == 'auto':
        if gates.get('enabled') is not True:
            raise ValueError('Auto model selection is disabled for this project')
        # Snapshot inherited/default-list resolution only when no complete local
        # binding exists. Re-selecting Auto must keep the same Pipeline default.
        if not (result.get('default_llm_model_name') and result.get('default_llm_model_project_id')):
            concrete = concrete_default or {}
            if not concrete.get('default_model_name') or not concrete.get('default_model_project_id'):
                raise ValueError('A concrete default model is required before enabling Auto')
            result['default_llm_model_name'] = concrete['default_model_name']
            result['default_llm_model_project_id'] = concrete['default_model_project_id']
        # Keep the concrete default for Pipelines, internal helpers and gate-off.
        result[AUTO_DEFAULT_KEY] = 'auto'
    else:
        result[f'default_{parsed.section}_model_name'] = parsed.name
        result[f'default_{parsed.section}_model_project_id'] = parsed.target_project_id
        if parsed.section == 'llm':
            result.pop(AUTO_DEFAULT_KEY, None)
    return result


def get_default_selection(project_id):
    from .local_tools import VaultClient
    from .routing_settings import get_effective_settings
    gates = get_effective_settings(project_id)
    if gates['enabled'] is not True:
        return None
    return effective_default_selection(VaultClient.from_project(project_id).get_secrets(), gates)
