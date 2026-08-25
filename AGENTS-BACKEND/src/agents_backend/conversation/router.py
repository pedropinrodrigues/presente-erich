from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DeterministicCommand:
    name: str
    answer: str


_HELP_TEXT = (
    "Posso consultar sua memória e suas pendências. Também posso encaminhar pedidos para guardar "
    "ou corrigir informações e, conforme novas integrações forem adicionadas, executar automações. "
    "Para ações, aviso primeiro que recebi o pedido e envio o resultado depois. Comandos de conta: "
    "/convidar, /convites, /revogar e /minhaconta."
)


def route_command(message: str) -> DeterministicCommand | None:
    command = message.strip().casefold().split(maxsplit=1)[0]
    if command in {"/ajuda", "/help", "/start"}:
        return DeterministicCommand(name="help", answer=_HELP_TEXT)
    return None
