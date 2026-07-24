"""Service prompt keys.

These keys are *predefined* and used by services to locate prompts.
Admins can configure the prompt *content* in DB, but cannot invent new keys.
"""

# Add new keys here as the platform grows.
SERVICE_PROMPT_KEYS: tuple[str, ...] = (
    'code_assistant',
    'decision_assistant',
    'edit_application_draft',
    'edit_skill_draft',
    'generate_application_draft',
    'llm_system_assistant',
    'llm_task_assistant',
    'mermaid_quick_fix',
    'printer_assistant',
    'project_context_generator',
    'router_assistant',
    'skill_generator',
    'state_modifier_assistant',
)
