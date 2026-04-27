from copy import deepcopy

from flask import request

from ...local_tools import APIBase
from ...models.pd.configuration import ConfigurationCreateBase
from ...models.pd.registry import CONFIG_TYPE_REGISTRY
#
#
class API(APIBase):
    url_params = [
        '',
        '<int:project_id>',
    ]
    def get(self, **kwargs):
        result = []
        section_filter = request.args.getlist("section")
        base_config_schema = ConfigurationCreateBase.model_json_schema()

        for entry in CONFIG_TYPE_REGISTRY.values():
            has_test_connection = bool(entry.check_connection_func) or hasattr(entry.model, "check_connection")
            if section_filter and entry.section not in section_filter:
                continue

            config_schema = deepcopy(base_config_schema)
            config_schema['properties'] = deepcopy(config_schema['properties'])

            # Extract check_connection_label from metadata if available
            check_connection_label = None
            if entry.config_schema:
                config_schema['properties']['data'] = entry.config_schema

                metadata = entry.config_schema.get('metadata', {})
                if 'label' in metadata:
                    config_schema['title'] = metadata['label']
                else:
                    config_schema['title'] = entry.config_schema.get('title')

                # Get custom button label for check_connection/load tools
                check_connection_label = metadata.get('check_connection_label')

                if entry.section in ['llm', 'embedding', 'image_generation']:
                    data_schema = config_schema['properties']['data']
                    if 'properties' in data_schema and 'ai_credentials' in data_schema['properties']:
                        if 'required' not in data_schema:
                            data_schema['required'] = []
                        if 'ai_credentials' not in data_schema['required']:
                            data_schema['required'].append('ai_credentials')

            result.append({
                "type": entry.type,
                "section": entry.section,
                "config_schema": config_schema,
                "has_test_connection": has_test_connection,
                "check_connection_label": check_connection_label,

                "validation_func": entry.validation_func,
                "check_connection_func": entry.check_connection_func,

            })
        return result, 200
