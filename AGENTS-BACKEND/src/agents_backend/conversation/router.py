from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DeterministicCommand:
    name: str
    answer: str


_HELP_TEXT = (
    "Oi! Eu sou a Luna, sua assistente de memória e ações.\n\n"
    "Você pode falar comigo normalmente ou enviar áudio — não precisa decorar comandos. Exemplos:\n"
    "• Guarde que a reunião do Projeto Atlas ficou para sexta.\n"
    "• Quais são minhas pendências?\n"
    "• Pesquise na internet as novidades sobre este assunto.\n"
    "• Amanhã às 9h, me lembre de ligar para a Marina.\n\n"
    "Comandos pessoais:\n"
    "• /minhaconta — mostra o estado da sua conta.\n"
    "• /macwhisper — cria sua URL pessoal do MacWhisper.\n"
    "• /revogarmacwhisper — invalida essa URL.\n"
    "• /ajuda — mostra esta orientação novamente.\n\n"
    "Administração, somente para usuários autorizados:\n"
    "• /convidar — cria um convite.\n"
    "• /convites — lista seus convites.\n"
    "• /revogar ID — revoga um convite pendente.\n\n"
    "Gestão de contas, somente para administradores:\n"
    "• /contas — lista as contas da plataforma.\n"
    "• /desativarconta ID — inicia a suspensão de uma conta.\n"
    "• /reativarconta ID — restaura uma conta suspensa.\n\n"
    "Se estiver em dúvida, diga o que deseja fazer e eu explico o próximo passo."
)


def route_command(message: str) -> DeterministicCommand | None:
    command = message.strip().casefold().split(maxsplit=1)[0]
    if command in {"/ajuda", "/help", "/start"}:
        return DeterministicCommand(name="help", answer=_HELP_TEXT)
    return None
