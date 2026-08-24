# Planejamento da skill de agendamentos e rotinas

## Estado da implementação

Implementado em 23/08/2026: contrato `ScheduleSpec v1`, RRULE/timezone, ativação imediata de avisos
pontuais R0 no chat de origem, confirmação única para as demais rotinas, autorização persistente por
revisão, CRUD conversacional, dispatcher transacional, runs com lease e idempotência, execução pelo
Terra, memória/perfil atual, entrega pela outbox e tools Composio já aprovadas de Gmail, Google
Calendar e WhatsApp Business. A migration ativa é `20260823_0007`.

O catálogo Composio extensível descrito na Etapa 5 e o deploy permanente da Etapa 6 continuam como
evoluções de produção. O worker local executa as rotinas somente enquanto estiver ligado.

## Objetivo

Permitir que o usuário crie, acompanhe, altere, pause e remova automações pontuais ou recorrentes
por conversa. Uma automação poderá consultar memória, usar tools locais, executar tools Composio
aprovadas e entregar o resultado no Telegram ou em outro canal autorizado.

Exemplos desejados:

- “amanhã às 9h me lembre de ligar para Ana”;
- “toda segunda às 8h prepare um resumo da semana”;
- “todo dia às 7h30 resuma meus compromissos de hoje e enriqueça com o que você sabe”;
- “na sexta envie este relatório pela minha conta Trabalho”;
- “pause a rotina do briefing diário”;
- “execute agora a rotina do briefing”.

“Executar qualquer coisa” significa compor qualquer tool local ou Composio que tenha sido
explicitamente admitida pela política do backend. Não significa entregar ao modelo um executor MCP
genérico, shell, proxy HTTP ou todo o catálogo sem classificação de risco.

## Decisões propostas

1. O backend será o relógio e a fonte de verdade. Composio Triggers continuará reservado para
   eventos que nascem em aplicativos, como um email recebido. Horários e recorrências serão
   persistidos e reivindicados pelo worker do projeto.
2. O agendamento é apenas o gatilho temporal. Quando chegar a hora, ele cria uma execução normal do
   orquestrador Terra com tools limitadas, auditoria, memória e outbox já existentes.
3. Um aviso pontual R0 que apenas responde no chat de origem é ativado pelo próprio pedido explícito
   do usuário. Rotinas recorrentes ou com outras tools exigem uma confirmação única; ela cria uma
   autorização persistente e limitada que não será solicitada novamente em cada ocorrência.
4. A autorização fica presa à versão da rotina, às tools, contas, destinatários e limites aprovados.
   Uma edição que amplie esse poder exige nova confirmação.
5. Leituras podem usar todas as contas conectadas. Escritas recorrentes ficam presas a uma conta
   específica; mudar a conta padrão não muda silenciosamente uma rotina existente.
6. A primeira versão não permitirá autorização permanente para exclusões, pagamentos, mudanças de
   permissão, administração de contas ou outras ações destrutivas/sensíveis.
7. Recorrência será armazenada como RFC 5545 RRULE mais um timezone IANA. `next_run_at` será UTC,
   recalculado a partir do horário local para respeitar mudanças de fuso e horário de verão.
8. O worker não fará uma tempestade de execuções após ficar offline. Por padrão, executará apenas a
   ocorrência perdida mais recente dentro da tolerância configurada.

## Arquitetura

```text
Mensagem do usuário
       │
       ▼
Luna identifica criação/alteração de rotina
       │ delega com interpretação e dúvidas relevantes
       ▼
Terra consulta contas/tools e compila ScheduleSpec v1
       │
       ▼
Backend valida tempo, política, escopo e autorização
       │
       ├─ aviso pontual R0 no próprio chat: ativa imediatamente
       │
       └─ demais rotinas: cria draft + PendingAction
                         │ usuário confirma uma vez
                         ▼
       ▼
ScheduledAutomation = active
       │ next_run_at chegou
       ▼
Dispatcher transacional ──► ScheduledRun idempotente
       │                         │
       │                         └─ cria ChannelMessage sintética, sem acknowledgement
       ▼
OrchestrationTask agendada
       │
       ▼
Terra usa memória + tools locais + tools Composio permitidas
       │
       ▼
Resultado auditado ──► Outbox ──► Telegram do próprio usuário
```

O worker atual já usa PostgreSQL, leases e `FOR UPDATE SKIP LOCKED`. O dispatcher deve seguir o
mesmo padrão de `claim_orchestration_task`, evitando um scheduler em memória. Isso permite múltiplos
workers sem duplicar ocorrências.

## Modelo de dados

### `scheduled_automations`

```text
id, workspace_id, user_id, conversation_id
name, original_request, compiled_spec JSON
timezone, recurrence_rule nullable, starts_at, ends_at nullable
next_run_at, last_run_at nullable
status (draft | awaiting_confirmation | active | paused | needs_attention | completed | expired | deleted)
misfire_policy (latest | skip), misfire_grace_seconds
max_runs nullable, run_count
revision, capabilities_snapshot JSON, tool_policy_snapshot JSON
created_at, updated_at, activated_at, paused_at, deleted_at
INDEX(status, next_run_at)
```

### `scheduled_runs`

```text
id, workspace_id, scheduled_automation_id, automation_revision
scheduled_for, status (queued | running | completed | retrying | failed | skipped)
attempts, max_attempts, available_at, lease_expires_at, locked_by
orchestration_task_id nullable, result_code, error_code
started_at, completed_at, created_at
UNIQUE(scheduled_automation_id, scheduled_for)
INDEX(status, available_at, scheduled_for)
```

### `automation_grants`

Registra a autorização criada pelo pedido explícito de um aviso pontual R0 ou pela confirmação das
demais rotinas:

```text
id, workspace_id, user_id, scheduled_automation_id, automation_revision
allowed_tools JSON, allowed_account_ids JSON, constraints JSON
max_risk, status (pending | active | revoked | expired)
confirmed_by_message_id, confirmed_at, revoked_at
```

Exemplos de `constraints` são destinatários fixos, canais de entrega, número máximo de envios por
ocorrência, limite de steps, limite de custo e proibição de anexos. O grant nunca conterá tokens.

### `schedule_events`

Audit trail append-only para criação, confirmação, disparo, atraso, retry, pausa, mudança de versão,
bloqueio e exclusão.

## Contrato compilado: `ScheduleSpec v1`

O texto original sempre será preservado, mas não será executado sozinho. Terra o converterá em um
contrato estruturado validado pelo backend:

```json
{
  "name": "Briefing diário",
  "trigger": {
    "kind": "recurring",
    "timezone": "America/Sao_Paulo",
    "recurrence_rule": "FREQ=DAILY;BYHOUR=7;BYMINUTE=30"
  },
  "objective": "Resumir os compromissos de hoje e adicionar contexto útil da memória",
  "context_policy": {
    "user_profile": true,
    "long_term_memory": true,
    "maximum_memory_queries": 8
  },
  "tool_policy": {
    "tools": ["calendar_list_events", "search_memory", "deliver_to_user"],
    "account_scope": "all_connected_accounts",
    "account_ids": [],
    "max_risk": "R0",
    "constraints": {
      "recipient_emails": [],
      "to_numbers": [],
      "maximum_external_writes_per_run": 1,
      "allow_attachments": false
    }
  },
  "delivery": {
    "kind": "originating_conversation",
    "only_when_empty": false
  }
}
```

O modelo poderá sugerir o contrato, mas o serviço de domínio valida:

- timezone, próxima ocorrência e RRULE;
- tools existentes e capacidades permitidas;
- contas pertencentes ao usuário/workspace;
- risco e constraints da autorização;
- limites de frequência, steps, tool calls e custo;
- destino de entrega vinculado ao próprio usuário;
- ausência de recursão, como uma rotina criar outras rotinas durante sua execução.

## Tools conversacionais

| Tool local | Risco | Responsabilidade |
| --- | --- | --- |
| `create_schedule` | R2 | Ativa avisos pontuais R0 no chat de origem; nas demais rotinas, cria draft e pede uma confirmação única. |
| `list_schedules` | R0 | Lista rotinas, estado e próxima execução. |
| `get_schedule` | R0 | Mostra contrato, autorização e histórico recente. |
| `update_schedule` | R1/R2 | Atualiza; exige nova confirmação quando ampliar poderes. |
| `pause_schedule` | R1 | Impede novos disparos sem apagar histórico. |
| `resume_schedule` | R1/R2 | Reativa; reconfirma apenas se grant/revisão não forem válidos. |
| `delete_schedule` | R1 | Soft delete explícito e revogação do grant. |
| `run_schedule_now` | R1 | Cria uma ocorrência manual idempotente usando o grant ativo. |
| `list_schedule_runs` | R0 | Mostra sucessos, falhas e atrasos recentes. |

`schedule_management` foi adicionado às capacidades de `AUTOMATION` e `COMPOUND`. Uma intenção
`SCHEDULE_MANAGEMENT` separada continua opcional caso os evals futuros mostrem ganho de precisão.

Luna deve pedir clarificação somente quando faltar algo material, como horário, timezone,
destinatário ou conta de uma escrita. Expressões suficientemente claras usam o timezone do usuário.
Para “me avise aqui daqui a um minuto”, o pedido inicial já é a autorização e não há uma segunda
confirmação; rotinas recorrentes ou com outros efeitos exibem a interpretação antes da confirmação.

## Autorização e confirmação

Um aviso pontual é ativado sem confirmação adicional somente quando todas estas condições forem
verdadeiras: trigger `once`, única tool `deliver_to_user`, entrega na conversa de origem, risco
máximo R0, nenhuma conta externa e nenhuma escrita externa. O pedido inicial fica auditado como a
autorização explícita dessa ocorrência.

As demais rotinas recebem uma confirmação única de ativação. A mensagem de confirmação deve mostrar:

- nome e objetivo;
- próxima execução e recorrência no horário local;
- tools e contas envolvidas;
- destinatários e canal de entrega;
- quais ações ocorrerão sem nova confirmação;
- como pausar ou excluir.

Após confirmar, cada run recebe no `ToolContext` o `scheduled_run_id` e o grant daquela revisão.
Tools R2 somente ignoram a confirmação por ocorrência quando `_execute_policy` comprovar que slug,
conta, destinatário e argumentos protegidos cabem integralmente no grant. Caso contrário, a ação é
bloqueada; o modelo não pode ampliar o grant.

Política inicial:

- R0 de leitura: permitida dentro das tools e contas aprovadas;
- entrega no chat do próprio usuário: permitida pelo grant;
- rascunhos R1: permitidos se explicitamente incluídos;
- email/mensagem R2: autorização permanente somente com conta e destinatários fixos;
- destinatário dinâmico, exclusão, pagamento, permissão e ação destrutiva: confirmação por
  ocorrência ou indisponível para rotina na primeira versão.

Se a confirmação de uma rotina pontual chegar depois do horário, mas ainda dentro de
`misfire_grace_seconds`, ela é ativada para execução imediata. Sem confirmação após essa tolerância,
a rotina passa a `expired`, seu grant é revogado e a ação pendente é encerrada.

## Catálogo amplo do Composio

O catálogo atual é fixo em `policies.py`. Para chegar a “qualquer skill do Composio” com segurança,
adicionar uma segunda camada de admissão:

1. `ComposioCatalogService` pesquisa tools e lê seus schemas apenas durante a criação/edição.
2. `approved_external_tools` persiste slug, toolkit, versão, schema hash, classificação R0/R1/R2,
   campos sensíveis, idempotência e constraints permitidas.
3. Tools somente de leitura poderão ser admitidas automaticamente quando tags e denylist forem
   compatíveis, ainda com schema e versão congelados.
4. Tools de escrita exigem uma política local explícita; tags remotas não são autoridade final.
5. Cada execução cria uma sessão MCP mínima contendo apenas os slugs daquela rotina.
6. Mudança de schema ou versão pausa a rotina até revalidação.

Continuam proibidos por padrão: remote bash/workbench, proxy HTTP genérico, administração de
credenciais, pagamentos, exclusões amplas e meta-tools que permitam descobrir e executar poder novo
durante um run.

Composio Triggers são complementares: servem para “quando chegar um email, faça X”, não para “todo
dia às 8h”. Eventos de trigger entrarão no mesmo `ScheduledRun`/workflow executor em uma fase
posterior, após validação de assinatura e deduplicação.

## Fluxo do briefing diário enriquecido

Para “todo dia de manhã envie um resumo dos meus compromissos de hoje enriquecido pela memória”:

1. O dispatcher cria uma ocorrência usando a data local do usuário.
2. O Terra consulta todos os Google Calendars conectados para `[00:00, dia seguinte 00:00)`.
3. O normalizador preserva conta, calendário, horário, título, descrição, organizador, convidados e
   status de resposta do próprio usuário.
4. Para cada compromisso relevante, o agente extrai pessoas, empresas, projetos e palavras-chave.
5. `search_memory` recupera fatos e compromissos atuais, sempre com evidências e limite por evento.
6. O Terra produz texto simples para Telegram, separando agenda, contexto útil, conflitos e pontos
   de preparação. Ausência de memória é declarada sem invenção.
7. O resultado é persistido como `ChannelMessage` outbound e entregue pela outbox.

O perfil geral do usuário é carregado novamente a cada run; a rotina não guarda uma cópia obsoleta
da memória. Conteúdo de eventos e emails permanece dado não confiável e não pode alterar a rotina ou
as políticas.

## Dispatcher e confiabilidade

Algoritmo de claim:

1. selecionar `active AND next_run_at <= now()` com `FOR UPDATE SKIP LOCKED`;
2. inserir `ScheduledRun` com chave única `(automation_id, scheduled_for)`;
3. calcular e persistir a próxima ocorrência na mesma transação;
4. criar uma `ChannelMessage` sintética já marcada como processada e uma `OrchestrationTask` sem
   mensagem intermediária de “estou verificando”;
5. deixar o worker normal reivindicar e executar a tarefa;
6. persistir resultado e enviar apenas a mensagem final.

Retries reutilizam a mesma ocorrência. Leitura idempotente pode repetir. Escrita não idempotente com
resultado incerto não repete automaticamente. Após a última falha, a rotina registra o erro e envia
uma única notificação útil; falhas repetidas de conexão movem a rotina para `needs_attention`.

Configurações propostas:

```text
SCHEDULER_ENABLED=true
SCHEDULER_POLL_INTERVAL_SECONDS=1
SCHEDULE_MAX_RUN_ATTEMPTS=3
SCHEDULE_DEFAULT_MISFIRE_GRACE_SECONDS=21600
SCHEDULE_MAX_TOOL_CALLS=12
SCHEDULE_MAX_CONCURRENT_RUNS_PER_USER=2
```

O worker Python precisa estar publicado e ativo 24/7. O Supabase hospedado persiste e coordena os
runs, mas não mantém o processo Python local acordado. Um cron externo pode acordar uma API, porém
não substitui o deploy permanente do worker.

## Observabilidade e controles

Métricas mínimas:

- atraso entre `scheduled_for` e `started_at`;
- taxa de sucesso por rotina/toolkit;
- runs ignorados por misfire;
- retries e `outcome_unknown`;
- tokens, duração e tool calls por ocorrência;
- entregas da outbox e rotinas em `needs_attention`.

Limites iniciais propostos, sem limitar o número de rotinas do usuário:

- no máximo uma ocorrência simultânea da mesma rotina;
- no máximo duas ocorrências simultâneas por usuário;
- frequência mínima de cinco minutos;
- no máximo 12 tool calls e 10 minutos por ocorrência;
- nenhuma recuperação ilimitada de ocorrências antigas.

Esses são limites operacionais por execução, não cotas de conta.

## Etapas de implementação

### Etapa 1 — fundação temporal

- migrations e modelos das quatro estruturas;
- parser/validador de `ScheduleSpec v1` e RRULE;
- cálculo de próxima ocorrência com timezone e testes de DST;
- claim, lease, idempotência, misfire e criação da tarefa sintética;
- listagem, pausa, retomada, exclusão e execução manual.

**Gate:** duas instâncias do worker não criam runs duplicados e uma reinicialização não causa
tempestade de catch-up.

### Etapa 2 — criação por conversa e autorização

- novas tools no orquestrador;
- ativação imediata do aviso pontual R0 pelo pedido inicial;
- draft e confirmação única pelo `PendingAction` para as demais rotinas;
- `AutomationGrant` versionado;
- resumo legível da próxima execução antes de ativar;
- evals de data, recorrência, pausa, atualização e cancelamento.

**Gate:** somente o aviso pontual R0 estritamente limitado fica ativo pelo pedido inicial; nenhuma
outra rotina ativa sem confirmação e nenhuma edição amplia o grant silenciosamente.

### Etapa 3 — briefing diário completo

- leitura Calendar multi-conta com convidados/status preservados;
- enriquecimento com perfil e memória atual;
- composição Telegram sem Markdown;
- notificação de falha e estado `needs_attention`;
- teste real de uma rotina diária em conta sandbox.

**Gate:** agenda completa, evidências coerentes e uma única entrega por ocorrência.

### Etapa 4 — escritas recorrentes

- contexto de grant no `ToolContext`;
- bypass estrito da confirmação por ocorrência;
- pin de conta e destinatários;
- idempotência/reconciliação por tool;
- email e mensagem como primeiros efeitos externos recorrentes.

**Gate:** uma tool fora do grant é bloqueada e um timeout incerto nunca duplica envio.

### Etapa 5 — catálogo Composio extensível

- descoberta controlada e registro de schemas/versionamento;
- política local e denylist;
- novas conexões guiadas quando a rotina exigir outro toolkit;
- admissão gradual de reads e depois writes aprovados;
- suporte posterior a Composio Triggers como gatilhos por evento.

**Gate:** o modelo nunca adquire uma tool não aprovada durante a execução.

### Etapa 6 — produção

- deploy permanente de API/worker;
- métricas, alertas, painel de runs e recuperação;
- testes de carga e concorrência;
- rollout por feature flag e piloto com rotinas não destrutivas.

## Testes obrigatórios

- once, diário, semanal, dias úteis, fim de mês e timezone com DST;
- RRULE inválida, data passada e horário inexistente/duplicado por DST;
- duplicate claim, worker morto durante lease e replay da mesma ocorrência;
- misfire dentro/fora da tolerância e ausência de catch-up storm;
- pausa ou exclusão concorrente com disparo;
- usuário/workspace cruzado e destino de canal de outra conta;
- tool, conta, destinatário ou argumento fora do grant;
- conta Composio revogada, schema alterado e resultado truncado;
- timeout antes/depois de email ou mensagem e `outcome_unknown`;
- prompt injection em evento, email ou memória;
- briefing sem eventos, com eventos sobrepostos, múltiplas contas e memória contraditória;
- texto final compatível com Telegram e outbox idempotente.

## Critérios de aceite

1. O usuário cria um aviso pontual pelo Telegram sem confirmação redundante e confirma uma única
   vez as rotinas recorrentes ou com outros efeitos.
2. A próxima ocorrência exibida corresponde ao timezone e à recorrência persistida.
3. Cada ocorrência lógica gera no máximo um `ScheduledRun`, uma tarefa e uma entrega final.
4. Pausar impede novos runs; retomar não recupera ocorrências antigas ilimitadamente.
5. A rotina usa somente tools, contas e destinatários presentes no grant confirmado.
6. Escrita recorrente não solicita confirmação por ocorrência quando estiver integralmente coberta
   pelo grant, e é bloqueada fora dele.
7. O briefing diário consulta todas as contas Calendar selecionadas e memória atual com evidências.
8. Falhas ficam auditáveis e não são comunicadas como sucesso.
9. Nenhuma credencial, header MCP ou token OAuth entra no contrato, log ou mensagem.
10. O sistema continua seguro com múltiplos workers e após reinicializações.

## Fontes técnicas

- [Composio Sessions e escopo por usuário](https://docs.composio.dev/docs/how-composio-works)
- [Configuração e allowlist de tools](https://docs.composio.dev/docs/configuring-sessions)
- [Composio Triggers](https://docs.composio.dev/docs/triggers)
- [Múltiplas contas conectadas](https://docs.composio.dev/docs/authentication/managing-multiple-connected-accounts)
