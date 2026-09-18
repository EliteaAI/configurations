"""Actor-visible model inventory for trusted Auto admission, without credentials.

Gateway owns authentication and project authorization. This internal reader requires
that actor explicitly so RPC/background execution never relies on Flask context.
Unlike the picker, routing must not merge limits from several configurations.
"""
import hashlib
import json

from .common_utils import get_public_project_id
from .folder_access import folder_exclusion_clause
from .local_tools import db
from .models.configuration import Configuration


MAX_ROUTING_CONFIGURATION_ROWS = 1024
CAPABILITY_FIELDS = (
    'context_window', 'max_output_tokens', 'supports_reasoning', 'supports_vision',
    'openai_compatible', 'low_tier', 'high_tier',
)


def _digest(value):
    encoded = json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
    return hashlib.sha256(encoded.encode('utf-8')).hexdigest()


def _read_rows(project_id, user_id, shared_only):
    # Select only inventory metadata, never Configuration.data or ai_credentials.
    fields = [
        Configuration.id.label('configuration_id'),
        Configuration.uuid.label('configuration_uuid'),
        Configuration.project_id,
        Configuration.shared,
        Configuration.status_ok,
        Configuration.label.label('display_name'),
        Configuration.updated_at,
        Configuration.created_at,
        Configuration.data['name'].astext.label('name'),
        *(Configuration.data[key].label(key) for key in CAPABILITY_FIELDS),
        Configuration.data['mid_tier'].label('legacy_mid_tier'),
    ]
    filters = [Configuration.project_id == project_id, Configuration.section == 'llm']
    if shared_only:
        filters.append(Configuration.shared.is_(True))
    excluded = folder_exclusion_clause(project_id, Configuration.id, user_id=user_id)
    if excluded is not None:
        filters.append(excluded)
    with db.get_session(project_id) as session:
        rows = session.query(*fields).filter(*filters).limit(MAX_ROUTING_CONFIGURATION_ROWS + 1).all()
        if len(rows) > MAX_ROUTING_CONFIGURATION_ROWS:
            raise ValueError('Routing model inventory exceeds the configuration row limit')
        return [dict(row._mapping) for row in rows]


def _model_record(row):
    name = row['name']
    if not isinstance(name, str) or not name.strip() or len(name) > 256:
        raise ValueError('Routing model configuration has an invalid model name')
    record = {
        'name': name,
        'project_id': row['project_id'],
        'configuration_id': row['configuration_id'],
        'configuration_uuid': str(row['configuration_uuid']),
        'display_name': str(row.get('display_name') or name)[:512],
        'shared': row['shared'] is True,
        'status_ok': row['status_ok'] is True,
        'configuration_updated_at': str(row.get('updated_at') or row.get('created_at') or ''),
        **{key: row.get(key) for key in CAPABILITY_FIELDS},
    }
    if record['high_tier'] is None:
        record['high_tier'] = row.get('legacy_mid_tier')
    for key in ('supports_reasoning', 'supports_vision', 'openai_compatible', 'low_tier', 'high_tier'):
        if type(record[key]) is not bool:
            record[key] = None
    for key in ('context_window', 'max_output_tokens'):
        if type(record[key]) is not int or record[key] <= 0:
            record[key] = None
    record['configuration_fingerprint'] = _digest(record)
    return record


def _select_model(name, rows):
    records = sorted((_model_record(row) for row in rows), key=lambda row: row['configuration_id'])
    if len(records) != 1:
        # No exact provider binding exists for this ambiguous owner/name. Keep a
        # shadow entry, but neither invent a winner nor merge its capabilities.
        identities = [{key: row[key] for key in (
            'configuration_id', 'configuration_uuid', 'configuration_fingerprint',
        )} for row in records]
        return {
            'name': name, 'project_id': records[0]['project_id'],
            'configuration_id': None, 'configuration_uuid': None,
            'configuration_fingerprint': _digest(identities),
            'configuration_identities': identities,
            'available': False, 'exclusion_reason': 'ambiguous_model_configuration',
        }
    selected = records[0]
    reason = None
    if not selected['status_ok']:
        reason = 'unhealthy_configuration'
    elif selected['context_window'] is None or selected['max_output_tokens'] is None:
        reason = 'invalid_model_limits'
    return {**selected, 'available': reason is None, 'exclusion_reason': reason}


def get_routing_models(project_id, user_id):
    """Current project overrides shared public names before health admission.

    There is deliberately no personal-project union. Folder-invisible rows never
    enter the snapshot or shadow set. Consumers must retain unavailable shadows
    while resolving precedence, then exclude them from routing candidates.
    """
    if type(project_id) is not int or project_id <= 0 or type(user_id) is not int or user_id <= 0:
        raise ValueError('Routing inventory requires a trusted project and user ID')
    public_project_id = get_public_project_id()
    current = _read_rows(project_id, user_id, shared_only=False)
    public = []
    if public_project_id and public_project_id != project_id:
        public = _read_rows(public_project_id, user_id, shared_only=True)
    by_name = {}
    for row in current:
        by_name.setdefault(row['name'], []).append(row)
    current_names = set(by_name)
    for row in public:
        if row['name'] not in current_names:
            by_name.setdefault(row['name'], []).append(row)
    items = [_select_model(name, rows) for name, rows in sorted(by_name.items())]
    snapshot = {'schema_version': 1, 'project_id': project_id, 'user_id': user_id,
                'public_project_id': public_project_id, 'items': items}
    return {**snapshot, 'revision': _digest(snapshot)}
