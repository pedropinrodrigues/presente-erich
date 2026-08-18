from __future__ import annotations

from agents_backend.models import OrchestrationIntent

ACKNOWLEDGEMENT = "Estou verificando isso e já te retorno."

INTENT_CAPABILITIES: dict[OrchestrationIntent, tuple[str, ...]] = {
    OrchestrationIntent.MEMORY_WRITE: ("memory_read", "ingestion"),
    OrchestrationIntent.MEMORY_CORRECTION: ("memory_read", "memory_correction"),
    OrchestrationIntent.MEMORY_DELETION: ("memory_read", "memory_deletion"),
    OrchestrationIntent.AUTOMATION: ("memory_read", "automation"),
    OrchestrationIntent.EXTERNAL_COMMUNICATION: (
        "memory_read",
        "external_communication",
    ),
    OrchestrationIntent.ACCOUNT_MANAGEMENT: ("account_management",),
    OrchestrationIntent.INVITE_MANAGEMENT: ("invite_management",),
    OrchestrationIntent.COMPOUND: (
        "memory_read",
        "ingestion",
        "memory_correction",
        "memory_deletion",
        "automation",
        "external_communication",
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
    # These capabilities deliberately have no tool until their domain integrations exist.
    "automation": (),
    "external_communication": (),
    "account_management": (),
    "invite_management": (),
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
