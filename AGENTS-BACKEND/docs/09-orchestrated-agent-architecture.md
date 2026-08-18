# Guia de implementação — agente rápido e orquestrador de tarefas

## Estado

Este documento define a próxima evolução da camada de agentes. O runtime atual possui um único
\`ConversationAgent\`: ele responde, consulta memória e também executa tools de escrita. A separação
descrita aqui ainda **não está implementada**.

Este guia deve ser executado antes de [Convites e contas pelo Telegram](10-telegram-invites-and-accounts.md).
O fluxo de convite usará as fronteiras, a fila de tarefas e a autorização descritas aqui.

## Objetivo

Manter perguntas sobre memória rápidas e econômicas, sem dar ao agente de conversa poderes de
automação ou alteração. Toda tarefa que exija mudança, integração externa ou workflow de domínio é
delegada a um orquestrador assíncrono, mais capaz e com catálogo próprio de tools.

O usuário continua falando com um único bot:

\`\`\`text
Pergunta simples                 Tarefa que exige ação
      │                                     │
      ▼                                     ▼
Resposta fundamentada                  “Estou verificando isso...”
no mesmo turno                              │
                                            ▼
                                  execução em segundo plano
                                            │
                                            ▼
                                  resposta final no mesmo chat
\`\`\`

Não haverá conversa livre entre agentes. O handoff é uma tarefa estruturada, persistida e auditável;
o orquestrador nunca usa texto livre do primeiro agente como autoridade.

## Decisões

- Há um **roteador determinístico**, um **agente rápido** e um **agente orquestrador**.
- Comandos conhecidos são tratados antes de chamar qualquer modelo.
- O agente rápido só pode consultar memória e criar uma tarefa; ele não altera domínio nem chama
  integrações externas.
- O orquestrador pode consultar memória e usar somente as tools autorizadas para a tarefa recebida.
- Ingestão, extração, e-mail e integrações são workflows/casos de uso, não agentes com poder geral.
- As duas mensagens ao usuário são persistidas e enviadas pela outbox.
- Usuário, workspace e permissões são sempre resolvidos pelo backend a partir do canal ou JWT.
- Toda ação continua tipada, validada, idempotente e auditável.
- Não haverá quota funcional por usuário. Limites de passos, tamanho, timeout e retry são controles
  técnicos de uma execução, não plano ou saldo de uso.

## Componentes

| Componente | Responsabilidade | Não pode fazer |
| --- | --- | --- |
| Roteador | Comandos, validação e rota determinística. | Executar tools livres. |
| Agente rápido | Consulta, resposta e decisão responder/delegar. | Escrever, apagar, automatizar ou integrar. |
| Serviço de tarefas | Handoff, estado, ordem de mensagens e idempotência. | Escolher tools ou redigir resposta. |
| Orquestrador | Planejar e executar tarefa autorizada. | Acessar banco, HTTP ou segredos diretamente. |
| Workflow de domínio | Regras concretas: ingestão, e-mail, lembrete, convite. | Decidir livremente ações. |
| Worker/outbox | Processar tarefas e entregar mensagens com retry. | Reinterpretar intenção. |

O agente rápido substitui o papel atual do \`ConversationAgent\` para o caminho de leitura. O
orquestrador é um runtime separado, não apenas um novo prompt com todas as tools.

## Fluxo

### Entrada e roteamento

\`\`\`text
Telegram ou HTTP autenticado
        │
        ▼
Gateway do canal / API
        │ valida assinatura/JWT e persiste mensagem idempotente
        ▼
Roteador determinístico
        ├─ comando conhecido ─────→ caso de uso direto
        └─ texto livre ───────────→ agente rápido
\`\`\`

Comandos como \`/ajuda\`, e futuramente \`/convidar\`, não usam modelo quando puderem ser atendidos
com segurança. Eles chamam o mesmo caso de uso que uma tool usaria e respondem pela outbox.

### Caminho rápido

O agente rápido recebe histórico, instruções de segurança e tools R0 de leitura. Em “o que foi
decidido sobre o Projeto Alfa?”, ele chama \`search_memory\`/\`get_entity\`, recebe evidências e
responde no mesmo processamento.

\`\`\`text
Mensagem → agente rápido → tools R0 → resposta final → outbox
\`\`\`

Ele declara incerteza quando faltarem evidências e nunca inventa fatos sobre o usuário.

### Delegação

Quando a mensagem exige alteração, automação ou integração, o agente rápido chama somente
\`delegate_to_orchestrator\`. Essa tool não realiza o pedido: ela persiste uma tarefa e uma mensagem
curta de recebimento.

\`\`\`text
“Guarde esta reunião e lembre-me de cobrar a Ana amanhã”
        │
        ▼
Agente rápido chama delegate_to_orchestrator
        │
        ├─ orchestration_task persistida
        ├─ outbox: “Estou verificando isso e já te retorno.”
        └─ worker do orquestrador
\`\`\`

O agente rápido não deve alegar sucesso. O orquestrador produz a conclusão, falha segura ou pedido
de esclarecimento.

### Execução e retorno

\`\`\`text
Worker reivindica orchestration_task
        │
        ▼
Orquestrador (modelo mais capaz)
        │ function calls estritas
        ▼
Casos de uso / integrações autorizadas
        │
        ▼
Resultado estruturado + auditoria
        │
        ▼
Mensagem final pela outbox → mesmo chat
\`\`\`

A outbox deve preservar a ordem: a mensagem final terá \`depends_on_outbox_id\` ou uma sequência
atômica por conversa apontando para a mensagem de recebimento. Retry não pode inverter as mensagens.

## Política de rota

O agente rápido escolhe somente:

\`\`\`text
answer_now
delegate_to_orchestrator
ask_clarifying_question
\`\`\`

| Intenção | Rota | Exemplo |
| --- | --- | --- |
| Pergunta sobre memória, fonte, entidade ou compromisso | \`answer_now\` | “Qual o prazo da proposta?” |
| Pergunta geral segura | \`answer_now\` | “Como você pode me ajudar?” |
| Salvar, corrigir, contestar ou apagar informação | Delegar | “Guarde isto” |
| Criar, mudar ou cancelar automação | Delegar | “Me lembre amanhã” |
| Agir fora do sistema | Delegar | “Envie um e-mail” |
| Administrar conta/canais/convites | Comando ou delegar | “Crie um convite” |
| Alteração ambígua | Esclarecer | “Arrume meu cadastro” |

O executor valida novamente intenção e argumentos. A segurança nunca pode depender apenas da
classificação do primeiro modelo.

## Contrato do handoff

\`delegate_to_orchestrator\` recebe argumentos mínimos. O modelo não informa \`user_id\`,
\`workspace_id\`, permissões, destino externo ou risco.

\`\`\`json
{
  "intent": "memory_and_reminder",
  "summary": "Guardar a reunião enviada e criar lembrete de cobrança para Ana amanhã.",
  "user_request": "Guarde esta reunião e me lembre de cobrar a Ana amanhã",
  "requires_clarification": false
}
\`\`\`

O serviço completa e persiste o envelope:

\`\`\`json
{
  "id": "uuid",
  "workspace_id": "resolvido pelo backend",
  "user_id": "resolvido pelo backend",
  "conversation_id": "uuid",
  "inbound_message_id": "uuid",
  "provider": "telegram",
  "destination": "chat vinculado",
  "intent": "memory_and_reminder",
  "allowed_capabilities": ["ingestion", "reminders", "memory_read"],
  "request_text": "texto original da mensagem",
  "summary": "resumo não autoritativo"
}
\`\`\`

\`intent\` é uma enum controlada pelo backend. Valores iniciais: \`memory_write\`,
\`memory_correction\`, \`memory_deletion\`, \`automation\`, \`external_communication\`,
\`account_management\` e \`invite_management\`.

O orquestrador recebe texto original, resumo auditável e capacidades compatíveis com a intenção. Se
o resumo não for sustentado pela mensagem original, ele deve pedir esclarecimento ou encerrar com
segurança.

## Persistência de tarefas

Criar \`orchestration_tasks\` em migration posterior à camada conversacional atual.

| Campo | Regra |
| --- | --- |
| \`id\` | UUID da tarefa. |
| \`workspace_id\`, \`user_id\`, \`conversation_id\` | Contexto obtido do backend. |
| \`inbound_message_id\` | Origem única da intenção. |
| \`intent\` | Enum controlada. |
| \`request_text\` | Mensagem original necessária à execução. |
| \`summary\` | Resumo não autoritativo e auditável. |
| \`allowed_capabilities\` | Lista calculada por política. |
| \`status\` | \`queued\`, \`running\`, \`waiting_confirmation\`, \`completed\`, \`failed\`, \`cancelled\`. |
| Retry | \`idempotency_key\`, tentativas, disponibilidade e lease. |
| Outbox | \`ack_outbox_id\` e \`result_outbox_id\`. |
| Resultado | \`result_code\`, \`error_code\`, timestamps. |

Criar \`orchestration_task_events\` append-only para criação, início, tool chamada, confirmação,
conclusão e falha. \`tool_executions\` deve receber \`orchestration_task_id\` anulável.

\`\`\`text
UNIQUE(workspace_id, idempotency_key)
UNIQUE(inbound_message_id) para uma delegação lógica por mensagem
INDEX(status, available_at)
INDEX(conversation_id, created_at DESC)
\`\`\`

Resultado persistido e \`ToolExecution\` concluída sempre prevalecem sobre nova execução do modelo.

## Catálogos de tools

### Agente rápido

\`\`\`text
search_memory
get_entity
get_source_status
list_open_commitments
get_pending_action
delegate_to_orchestrator
\`\`\`

As cinco primeiras são R0. A tool de delegação só cria tarefa e acknowledgment; ela não aceita nome
arbitrário de tool nem capacidades fornecidas pelo modelo.

### Orquestrador

O catálogo é limitado por intenção, nunca global.

| Intenção | Capacidades/tools iniciais |
| --- | --- |
| \`memory_write\` | \`submit_transcript\`, consultas de memória. |
| \`memory_correction\` | \`correct_memory\`, consultas de memória. |
| \`memory_deletion\` | \`delete_memory\`, \`delete_source\`, \`confirm_action\`. |
| \`automation\` | Tools de lembrete/automações quando existirem. |
| \`external_communication\` | Preparar, confirmar e enviar comunicação quando a integração existir. |
| \`invite_management\` | Criação/listagem/revogação após o guia 10. |

Schema Pydantic estrito, validação de domínio, idempotência e \`RequestContext\` seguem obrigatórios.
Nenhum runtime recebe SQL, HTTP genérico, segredos ou contexto fornecido pelo cliente.

## Risco e confirmações

| Classe | Exemplo | Política |
| --- | --- | --- |
| R0 | Buscar decisão. | Executar imediatamente. |
| R1 | Salvar transcrição, criar lembrete. | Intenção explícita e auditoria. |
| R2 | Excluir memória ou ação externa irreversível. | Ação pendente e confirmação em outro turno. |

Para comunicação externa, começar com “preparar → mostrar resumo → confirmar → enviar”. Uma
confirmação só retoma a tarefa/ação pendente do mesmo usuário, workspace e conversa.

## Workflows de domínio

O orquestrador coordena; serviços executam regras concretas:

\`\`\`text
submit_transcript
  → ingest_transcript
  → Source + Job
  → worker de extração estruturada
  → memória, evidências e embeddings

send_email (futuro)
  → validar destinatário e política
  → outbox de e-mail idempotente
  → provedor de e-mail
  → registrar entrega
\`\`\`

O processamento de transcrição permanece determinístico e testável. Não criar um “agente de
ingestão” com poder geral; criar uma tool estreita que aciona o caso de uso atual.

## Modelos e configuração

Separar configuração e telemetria:

\`\`\`text
OPENAI_MODEL_CONVERSATION=...
OPENAI_REASONING_EFFORT_CONVERSATION=...
OPENAI_MODEL_ORCHESTRATION=...
OPENAI_REASONING_EFFORT_ORCHESTRATION=...
ORCHESTRATION_MAX_STEPS=...
ORCHESTRATION_MAX_TOOL_CALLS=...
ORCHESTRATION_TASK_MAX_ATTEMPTS=...
\`\`\`

O modelo de conversa prioriza latência e decisão responder/delegar. O modelo do orquestrador pode
ser mais capaz, pois é chamado apenas em tarefas de ação. Cada \`agent_run\` deve guardar tipo
(\`conversation\` ou \`orchestration\`), modelo, prompt, duração, tokens, task ID e resultado.

## Alterações esperadas no código

| Componente | Alteração |
| --- | --- |
| \`conversation/runtime.py\` | Runtime do agente rápido, com tools de leitura. |
| \`conversation/tools.py\` | Separar registries e adicionar delegação. |
| \`conversation/service.py\` | Roteador, acknowledgment e respostas rápidas. |
| \`orchestration/runtime.py\` | Novo loop de function calling. |
| \`orchestration/service.py\` | Criar, reivindicar, executar e concluir tarefas. |
| \`orchestration/policies.py\` | Intenção, capacidades, risco e mensagem curta. |
| \`models.py\` | Tarefas, eventos e FK de \`ToolExecution\`. |
| \`worker/main.py\` | Reivindicar tarefas e preservar prioridade de entrega. |
| \`worker/service.py\` | Manter extração como workflow separado. |
| \`evaluation/\` | Dataset de roteamento e avaliação do orquestrador. |

## Plano de implementação

### Etapa 1 — contrato e dados

- Criar enum de intenções, políticas de capacidade e schemas estritos.
- Adicionar tarefas, eventos e FK opcional em \`tool_executions\`.
- Criar migration e índices, sem mudar fluxo ativo.

**Gate:** tarefa pode ser criada, reprocessada e auditada sem modelo ou tool de domínio.

### Etapa 2 — agente rápido restrito

- Separar tools atuais em leitura e ação.
- Remover escrita/destruição do runtime de conversa.
- Adicionar \`delegate_to_orchestrator\` e prompt de decisão.
- Manter \`POST /v1/agent/turns\` para testar caminho rápido.

**Gate:** escrita cria tarefa, mas não altera memória pelo agente rápido.

### Etapa 3 — worker e orquestrador

- Implementar leasing, retry e estados de tarefa.
- Criar runtime separado e catálogo por política.
- Migrar \`remember_transcript\`, correções, contestações e exclusões ao orquestrador.

**Gate:** guardar transcrição cria \`Source\` e job de extração; replay não duplica fonte/tarefa.

### Etapa 4 — experiência de duas mensagens

- Criar acknowledgment na mesma transação da tarefa.
- Criar resultado final vinculado à conclusão.
- Implementar dependência/ordem de outbox por conversa.

**Gate:** mensagem curta sempre chega antes da conclusão, inclusive em retry.

### Etapa 5 — confirmação e comandos

- Vincular \`pending_actions\` a tarefas quando necessário.
- Garantir confirmação posterior no mesmo contexto.
- Adicionar comandos determinísticos úteis.

**Gate:** nenhuma exclusão ou envio externo ocorre sem a política correspondente.

### Etapa 6 — avaliação e publicação

- Criar avaliações para resposta, delegação e esclarecimento.
- Medir latência inicial, conclusão, tools, falhas e replays.
- Aplicar migration e fazer smoke test real no Telegram.

**Gate:** caminho rápido não invoca orquestrador; tarefas delegadas são idempotentes e retornam
desfecho.

## Testes obrigatórios

- pergunta de memória usa apenas R0 e responde no mesmo turno;
- pedido de salvar, corrigir, apagar, automatizar ou comunicar cria tarefa;
- comando conhecido não chama modelo;
- pedido ambíguo de alteração pede esclarecimento;
- mesma mensagem não cria duas tarefas;
- retry não repete tool concluída;
- agente rápido não possui tools de escrita;
- capacidades nunca vêm do cliente ou modelo;
- confirmação respeita usuário, workspace e conversa;
- acknowledgment precede mensagem final mesmo com retry;
- falha da outbox não reexecuta tarefa;
- não existe regra de quota bloqueando usuário por volume.

## Critérios de aceite

1. Consultas de memória são respondidas sem invocar o orquestrador.
2. Mudanças e automações geram tarefa persistida antes de produzir efeito.
3. O usuário recebe acknowledgment e desfecho posterior para tarefas.
4. O orquestrador recebe apenas capacidades permitidas pela intenção.
5. Nenhum agente tem acesso direto a banco, HTTP genérico, segredos ou identidade.
6. Replays não duplicam alteração, tarefa ou envio.
7. Ações de risco alto exigem confirmação posterior no contexto correto.
8. Ingestão e extração continuam workflows independentes.
9. Métricas distinguem agente rápido, orquestrador, extração e entrega.
10. Não há quota funcional diferenciando usuários, convidados ou criadores de convite.

## Fora deste guia

- implementar integrações específicas de e-mail, calendário ou automações;
- múltiplos workspaces colaborativos;
- acesso direto de modelos ao Supabase ou a provedores externos;
- quotas, cobrança ou planos;
- substituir workflows de extração por agentes livres.
