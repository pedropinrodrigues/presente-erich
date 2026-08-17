from __future__ import annotations

import re

from agents_backend.errors import AppError


def normalize_phone_number(value: str) -> str:
    normalized = re.sub(r"\D", "", value)
    if len(normalized) < 8 or len(normalized) > 20:
        raise AppError("invalid_phone_number", "O número de WhatsApp é inválido.", 400)
    return normalized


def whatsapp_phone_aliases(value: str) -> tuple[str, ...]:
    """Return identifiers Meta may use for the same WhatsApp number.

    Meta can expose Brazilian mobile numbers either with or without the ninth
    digit after the two-digit area code. Both variants are lookup aliases; the
    E.164 number supplied by the user remains the outbound destination.
    """
    normalized = normalize_phone_number(value)
    aliases = [normalized]
    if normalized.startswith("55"):
        if len(normalized) == 13 and normalized[4] == "9":
            aliases.append(normalized[:4] + normalized[5:])
        elif len(normalized) == 12:
            aliases.append(normalized[:4] + "9" + normalized[4:])
    return tuple(aliases)


def phone_numbers_equivalent(left: str, right: str) -> bool:
    return bool(set(whatsapp_phone_aliases(left)) & set(whatsapp_phone_aliases(right)))
