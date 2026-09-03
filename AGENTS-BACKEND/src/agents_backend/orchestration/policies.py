from __future__ import annotations

from agents_backend.models import OrchestrationIntent

ACKNOWLEDGEMENT = "Estou verificando isso e já te retorno."

INTENT_CAPABILITIES: dict[OrchestrationIntent, tuple[str, ...]] = {
    OrchestrationIntent.MEMORY_WRITE: ("memory_read", "ingestion"),
    OrchestrationIntent.MEMORY_CORRECTION: ("memory_read", "memory_correction"),
    OrchestrationIntent.MEMORY_DELETION: ("memory_read", "memory_deletion"),
    OrchestrationIntent.AUTOMATION: (
        "memory_read",
        "automation",
        "schedule_management",
        "integration_connection",
        "integration_read",
        "integration_execute",
        "bitrix_connection",
        "bitrix_task_read",
        "bitrix_task_execute",
        "invite_management",
    ),
    OrchestrationIntent.EXTERNAL_COMMUNICATION: (
        "memory_read",
        "external_communication",
        "integration_connection",
        "integration_read",
        "integration_draft",
        "integration_execute",
        "bitrix_connection",
        "bitrix_crm_read",
        "bitrix_crm_execute",
    ),
    OrchestrationIntent.ACCOUNT_MANAGEMENT: (
        "account_management",
        "integration_connection",
        "integration_read",
        "bitrix_connection",
    ),
    OrchestrationIntent.INVITE_MANAGEMENT: ("invite_management",),
    OrchestrationIntent.WEB_RESEARCH: ("web_research",),
    OrchestrationIntent.COMPOUND: (
        "memory_read",
        "ingestion",
        "memory_correction",
        "memory_deletion",
        "automation",
        "schedule_management",
        "external_communication",
        "integration_connection",
        "integration_read",
        "integration_draft",
        "integration_execute",
        "bitrix_connection",
        "bitrix_crm_read",
        "bitrix_crm_execute",
        "bitrix_task_read",
        "bitrix_task_execute",
        "web_research",
    ),
}

CAPABILITY_TOOLS: dict[str, tuple[str, ...]] = {
    "memory_read": (
        "search_memory",
        "get_entity",
        "get_source_status",
        "list_open_commitments",
        "get_pending_action",
    ),
    "ingestion": ("remember_transcript",),
    "memory_correction": ("correct_memory", "dispute_memory"),
    "memory_deletion": (
        "delete_memory",
        "delete_source",
        "confirm_action",
        "cancel_action",
    ),
    "automation": (),
    "schedule_management": (),
    "external_communication": (),
    "account_management": (),
    "integration_connection": (),
    "integration_read": (),
    "integration_draft": (),
    "integration_execute": ("confirm_action", "cancel_action", "get_pending_action"),
    "bitrix_connection": (),
    "bitrix_crm_read": (),
    "bitrix_crm_execute": ("confirm_action", "cancel_action", "get_pending_action"),
    "bitrix_task_read": (),
    "bitrix_task_execute": ("confirm_action", "cancel_action", "get_pending_action"),
    "invite_management": (
        "create_user_invite",
        "list_user_invites",
        "revoke_user_invite",
        "get_my_account",
    ),
    "web_research": ("research_web",),
}


def capabilities_for_intent(intent: OrchestrationIntent | str) -> list[str]:
    normalized = intent if isinstance(intent, OrchestrationIntent) else OrchestrationIntent(intent)
    return list(INTENT_CAPABILITIES[normalized])


def tool_names_for_capabilities(capabilities: list[str]) -> set[str]:
    return {
        tool_name
        for capability in capabilities
        for tool_name in CAPABILITY_TOOLS.get(capability, ())
    }
