"""Service prompt keys.

These keys are *predefined* and used by services to locate prompts.
Admins can configure the prompt *content* in DB, but cannot invent new keys.
"""

# Add new keys here as the platform grows.
SERVICE_PROMPT_KEYS: tuple[str, ...] = (
    'code_assistant',
    'decision_assistant',
    'llm_system_assistant',
    'llm_task_assistant',
    'mermaid_quick_fix',
    'printer_assistant',
    'router_assistant',
    'state_modifier_assistant',
)
