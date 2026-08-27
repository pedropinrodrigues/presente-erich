# Plano de integração Composio MCP Gateway

> Estado em 23/08/2026: fundação, allowlist, sessões MCP `direct-tools`, conexão pelo chat,
> callback Supabase, auditoria criptografada, confirmação R2 e múltiplas contas Gmail/Calendar
> implementados. Revogação, triggers de entrada e telemetria avançada permanecem para uma etapa
> posterior.

## Objetivo

Permitir que o orquestrador Terra use produtos externos já integrados pelo Composio — e, depois,
MCPs próprios registrados no Composio — sem entregar credenciais ao backend, ao modelo ou ao canal
Telegram. A integração deve conservar as propriedades que já existem no projeto:

- catálogo de tools limitado pela intenção e por política do backend;
- isolamento por workspace e usuário;
- confirmação única para efeitos externos relevantes ou irreversíveis;
- idempotência, auditoria e resultado persistido;
- resposta final pelo mesmo canal do usuário;
- nenhuma execução externa diretamente pelo Luna.

O primeiro escopo abrange integrações de leitura, rascunho e ação em produtos de terceiros. Gatilhos
de entrada do Composio (webhooks/automations recorrentes) entram somente depois que a execução de
tools estiver estável.

## Decisão arquitetural

Usar **Composio Sessions via MCP**, mas nunca conectar a URL MCP hospedada pelo Composio diretamente
ao modelo OpenAI.

```text
Telegram / HTTP
        │
        ▼
Luna ── delega ──► OrchestrationTask persistida
                         │
                         ▼
                 Orquestrador Terra
                         │ function call (tool local controlada)
                         ▼
            ComposioMcpGateway do backend
              │ cria/reusa sessão Composio por usuário
              │ MCP Streamable HTTP autenticado
              ▼
       Composio Session MCP endpoint
              │ OAuth/API key da conta conectada
              ▼
      Gmail / Calendar / Notion / GitHub / ...
```

O endpoint MCP da sessão do Composio é a camada que agrega os produtos. O adaptador local é a
fronteira de segurança: descobre somente tools permitidas, valida argumentos, aplica confirmação,
persiste a intenção antes do efeito e registra o resultado. Essa escolha é necessária porque o
uso direto do endpoint MCP por um cliente de modelo contorna os hooks/modificadores do SDK do
Composio; neste projeto, confirmações e auditoria não podem depender de um caminho contornado.

O antigo MCP API de servidores do Composio está depreciado; sessões com `mcp=True` são a interface
atual. Cada sessão fornece URL e headers MCP, e pode ser reutilizada por conversa/tarefa do mesmo
usuário. Não persistir URL ou headers MCP: eles são credenciais de sessão transitórias.

## Princípios de segurança

1. **Deny by default.** Nenhum toolkit, tool ou conta conectada fica disponível apenas por existir
   no Composio.
2. **Identidade opaca e estável.** Derivar `composio_user_id` com HMAC de `workspace_id` e
   `user_id`, usando segredo próprio; nunca enviar UUIDs internos em claro ao provedor.
3. **Escopo mínimo.** Criar a sessão com `sandbox.enable=false`, uma allowlist de toolkits e uma
   allowlist de tools por política. Não expor `REMOTE_BASH`, workbench, proxy HTTP genérico ou
   `MANAGE_CONNECTIONS` ao Terra na primeira versão.
4. **Autorização fora do modelo.** O backend cria Connect Links e os envia pela outbox. O modelo não
   escolhe auth config, conta conectada, callback URL ou escopo OAuth.
5. **Risco declarado pelo backend.** Cada tool aprovada terá classificação local R0/R1/R2, não
   inferida de sua descrição remota. R2 exige uma confirmação explícita em nova mensagem.
6. **Segredos nunca persistidos.** Não gravar tokens OAuth, headers MCP, Connect Link completo ou
   resultados com segredos. Armazenar somente IDs externos, status e versões, com campos
   sanitizados.
7. **Falha incerta não é sucesso.** Se o backend cair após enviar uma ação externa e antes de
   registrar o resultado, marcar `outcome_unknown`; não repetir automaticamente uma escrita sem
   chave idempotente remota ou consulta de reconciliação.

## Modelo de permissão

Adicionar uma política declarativa, versionada no código, semelhante a `orchestration/policies.py`.
Cada entrada deve definir:

| Campo | Exemplo |
| --- | --- |
| toolkit | `gmail` |
| tool slug | `GMAIL_CREATE_EMAIL_DRAFT` |
| intenção permitida | `external_communication` |
| capacidade | `integration_draft` |
| risco | `R1` |
| confirmação | não, para rascunho; sim, para envio |
| auth config | ID aprovado do Composio, quando aplicável |
| campos redigidos | corpo, anexos, destinatários, tokens |
| estratégia de idempotência | chave externa, reconciliação ou não repetível |

O catálogo inicial deve ser decidido explicitamente antes da implementação. Sugestão para piloto:

| Fase | Exemplos | Política |
| --- | --- | --- |
| leitura R0 | buscar e-mail, listar eventos, buscar página | executar após pedido explícito |
| rascunho R1 | criar rascunho de e-mail, preparar evento | executar e mostrar resumo |
| envio/alteração R2 | enviar e-mail, criar/editar evento, criar issue | mostrar resumo e pedir uma confirmação |
| destrutivo R2 | cancelar evento | implementado com confirmação; apagar e-mail/arquivo continua fora do piloto |

Não usar `toolkits` sem filtro nem `preload.tools=all`: além de ampliar poder indevidamente, isso
infla o contexto. A documentação do Composio recomenda manter o conjunto pré-carregado pequeno.

## Dados e migration implementados

A migration `20260819_0005_composio_integrations.py`, complementada pela migration multi-conta
`20260823_0006_multi_account_integrations.py`, criou as estruturas abaixo. Os invariantes continuam
sendo parte do contrato do projeto.

### `external_integrations`

Espelho não autoritativo de uma conexão Composio.

```text
id, workspace_id, user_id
provider = "composio"
toolkit_slug, auth_config_id, connected_account_id
status (pending | active | expired | revoked | failed)
display_name, account_label, is_default, metadata_sanitized
created_at, updated_at, last_verified_at, revoked_at
UNIQUE(workspace_id, user_id, provider, connected_account_id)
INDEX(workspace_id, user_id, toolkit_slug, status)
```

### `external_connection_requests`

Controla o Connect Link sem persistir o URL ou segredo.

```text
id, workspace_id, user_id, integration_id nullable
provider, toolkit_slug, auth_config_id
status (created | delivered | completed | expired | failed)
composio_request_id, callback_state_hash, expires_at
outbox_id nullable, created_at, completed_at
UNIQUE(workspace_id, callback_state_hash)
```

### `external_actions`

É o registro de intenção e execução para qualquer efeito de terceiro.

```text
id, workspace_id, user_id, conversation_id, orchestration_task_id
provider, toolkit_slug, tool_slug, risk_level
arguments_sanitized, arguments_hash, idempotency_key
status (proposed | confirmed | executing | succeeded | failed | outcome_unknown | cancelled)
composio_execution_id nullable, result_sanitized nullable, error_code nullable
pending_action_id nullable, created_at, confirmed_at, executed_at, completed_at
UNIQUE(workspace_id, idempotency_key)
INDEX(orchestration_task_id, created_at)
```

`ExternalAction`, `ToolExecution` e `PendingAction` preservam a relação entre intenção, confirmação,
execução e auditoria. O despachante de confirmação reconhece ações externas e ativa o executor
estrito correspondente sem entregar a escolha do handler ao modelo.

## Componentes implementados e extensíveis

```text
src/agents_backend/integrations/composio/
  gateway.py            descoberta, chamada e normalização de erros
  policies.py           allowlist, risco, confirmação e redaction por tool
  service.py            casos de uso: conectar, listar, executar, reconciliar
  results.py            compactação e normalização segura dos resultados MCP

supabase/functions/composio-callback/
  index.ts              callback público e validação do estado OAuth
  core.ts               regras testáveis do callback
```

As dependências do SDK Composio e do cliente MCP estão fixadas por faixa no `pyproject.toml`. O SDK
cria sessões e Connect Links; o gateway usa URL e headers MCP apenas em memória. O transporte fica
atrás do gateway e pode ser substituído por fake nos testes sem rede.

## Fluxos

### 1. Conectar uma conta

```text
Usuário: “conecte meu Gmail”
  → Luna delega account_management
  → Terra pede connect_gmail (tool local R1)
  → serviço confere allowlist e cria sessão/Connect Link Composio
  → external_connection_request + outbox são persistidos
  → Telegram envia o link
  → callback/webhook validado atualiza external_integrations para active
  → bot confirma que a conta está disponível
```

O callback recebe `state` assinado/hasheado e valida usuário, workspace, request, expiração e
toolkit. Nunca confiar em `user_id` fornecido na query string. Eventos de conta expirada devem
marcar a integração local e enviar um novo link somente após autorização do usuário ou conforme
política definida.

### 2. Leitura externa

```text
Usuário: “quais e-mails da Ana chegaram hoje?”
  → Luna delega external_communication
  → Terra recebe somente tools Gmail R0 permitidas
  → adaptador recupera/reusa sessão do usuário e executa pelo MCP
  → resultado sanitizado é registrado em ToolExecution + ExternalAction
  → Terra responde no Telegram
```

Quando houver mais de uma conta Gmail ou Google Calendar, `account_id=null` executa a leitura em
todas as conexões ativas e combina os resultados com identificação da conta. Um `account_id`
explícito limita a consulta. Escritas sem seleção explícita usam a conta marcada como padrão; a
confirmação R2 mostra o apelido da conta escolhida.

### 2.1 Gerenciar múltiplas contas

```text
Usuário: “adicione outra conta Gmail e chame de Trabalho”
  → Terra usa connect_external_app(add_another=true, account_label="Trabalho")
  → novo OAuth preserva as conexões existentes
  → callback ativa a nova ExternalIntegration
  → list_external_accounts mostra todas as contas e a padrão
  → configure_external_account pode alterar apelido ou conta padrão
```

### 3. Ação externa confirmada

```text
Usuário: “envie este e-mail para Ana”
  → Terra coleta dados e produz ExternalAction proposed
  → bot mostra destinatário, assunto e resumo do corpo
  → usuário confirma claramente em novo turno
  → PendingAction confirma ExternalAction
  → worker executa uma única vez via Composio MCP
  → resultado persistido e resposta final enfileirada
```

O orquestrador nunca deve chamar diretamente uma tool R2 durante o primeiro turno. Rascunhos são
preferíveis ao envio quando o produto oferecer ambos.

## Integração com o runtime do orquestrador

1. Adicionar capacidades `integration_read`, `integration_draft` e `integration_execute` à política
   de intenções, sem habilitá-las globalmente.
2. Resolver a allowlist por `task.intent`, `workspace_id`, usuário e integrações ativas.
3. Converter somente tools remotas aprovadas em definições OpenAI estritas. Validar schemas MCP
   recebidos contra um subconjunto suportado; uma tool com schema incompatível fica indisponível e
   gera telemetria, nunca um schema frouxo com `dict[str, Any]`.
4. Implementar `ComposioToolSpec`/adaptador compatível com `ToolRegistry`: validação de argumentos,
   hash, sanitização, audit trail e executor remoto. Não entregar ao modelo a tool MCP genérica
   `execute` nem meta-tools de descoberta.
5. Fazer o modelo ver nomes estáveis internos, por exemplo `gmail_search_messages`, e mapear esses
   nomes a slugs/versionamento do Composio somente no backend.
6. Antes de executar escrita, criar `ExternalAction` e `PendingAction` na mesma transação. A chamada
   remota ocorre após a confirmação e tem chave de idempotência derivada de `ExternalAction.id`.
7. Registrar eventos `connection_requested`, `connection_active`, `external_action_proposed`,
   `external_action_executing`, `external_action_succeeded`, `external_action_failed` e
   `external_action_outcome_unknown` em `orchestration_task_events`.

## Endpoints e canal

O fluxo inicial é chat-first, coerente com a arquitetura do produto. `connect_external_app` cria o
Connect Link como tool do orquestrador e a Edge Function pública
`/functions/v1/composio-callback` finaliza a autorização com state de uso único. Rotas REST de
listagem e revogação ficam para a interface administrativa futura; a revogação deverá ser R2.

No Telegram, links são enviados apenas como resultado de uma tarefa persistida. A URL não deve ir
para logs nem para `ChannelMessage.message_metadata`; somente o texto da outbox a carrega até o
provedor de mensageria.

## Implementação por etapas

### Etapa 0 — decisão de produto e Composio — concluída

- Escolher 2–3 toolkits do piloto e as tools exatas de cada um.
- Criar projeto Composio, API key de servidor e auth configs; definir callback HTTPS público.
- Documentar escopos OAuth mínimos e classificação de risco por tool.
- Criar contas de teste isoladas; nunca iniciar com conta pessoal de produção.

**Gate:** catálogo explícito aprovado; nenhuma tool de envio, exclusão ou proxy genérico liberada.

### Etapa 1 — fundação, dados e cliente fake — concluída

- Adicionar configurações: `COMPOSIO_API_KEY`, `COMPOSIO_CALLBACK_URL`,
  `COMPOSIO_USER_ID_SECRET`, `COMPOSIO_ENABLED`, timeout e allowlist versionada.
- Criar migration, models e repositórios das três estruturas de dados.
- Implementar protocolos `ComposioClient` e `McpTransport`, fake determinístico e sanitizador.
- Adicionar health check opcional que não teste credenciais nem crie sessão.

**Gate:** testes de isolamento entre usuários/workspaces, nenhum segredo serializado e migration
reversível em ambiente local.

### Etapa 2 — conexão e ciclo de vida — parcial

- Implementar criação de Connect Link, callback assinado e espelho de conta conectada.
- Adicionar listagem e revogação; processar expiração e reconexão.
- Entregar o link pelo Telegram usando outbox e auditar cada transição.

**Gate:** um usuário não pode usar, listar ou reconectar conta de outro; callback repetido é
idempotente.

Conexão, callback, expiração e múltiplas contas estão implementados. Revogação iniciada pelo
produto continua pendente.

### Etapa 3 — MCP adapter e leitura R0 — concluída para o catálogo inicial

- Criar/reusar sessão Composio com `mcp=True`, sandbox desligado e allowlist curta.
- Implementar descoberta MCP, conversão de schema, timeout, redaction e execução de leitura R0.
- Expor uma única integração de leitura de ponta a ponta com conta sandbox.

**Gate:** o Terra só enxerga tool aprovada; falha de conexão é explicada sem vazar URL/headers;
repetição da mesma mensagem não chama o provedor duas vezes.

### Etapa 4 — rascunhos e ações confirmadas — concluída para o catálogo inicial

- Introduzir `ExternalAction` e confirmação genérica no `PendingAction`.
- Implementar uma tool de rascunho R1 e uma ação R2 com resumo pré-confirmação.
- Implementar idempotência remota ou reconciliação; testar queda entre chamada e commit.

**Gate:** nenhuma ação R2 ocorre sem confirmação posterior clara; retries não duplicam efeitos.

### Etapa 5 — operação, avaliações e rollout — parcial

- Telemetria por ferramenta: discovery, auth, MCP, execução, confirmação, p50/p95, erro e custo.
- Evals com ênfase em injeção de prompt em e-mails/documentos, tool não permitida, schema remoto
malformado, conta expirada, cross-workspace e resultado incerto.
- Feature flag por toolkit e por workspace; piloto com uma conta sandbox, depois allowlist gradual.

**Gate:** qualidade, segurança e latência medidas em casos reais antes de habilitar novos produtos.

## Casos de teste obrigatórios

- usuário A tenta usar `connected_account_id` do usuário B;
- modelo pede uma tool Composio não permitida;
- conteúdo de e-mail instrui o agente a ignorar políticas;
- Connect Link/callback repetido, expirado ou com state inválido;
- schema MCP opcional/incompatível;
- timeout antes e depois da execução externa;
- envio R2 sem confirmação, com confirmação ambígua e com confirmação explícita;
- replay do mesmo `inbound_message_id`;
- token OAuth, URL MCP, headers e payload sensível ausentes de logs, eventos e respostas;
- conta expirada, revogada e com múltiplas contas do mesmo toolkit.

## Fora do escopo inicial

- expor o Composio MCP diretamente ao OpenAI/Telegram;
- liberar todo o catálogo do Composio ou proxy HTTP genérico;
- execução de shell/workbench remoto;
- triggers/automations inbound e Custom MCP experimental;
- anexos grandes e sincronização em lote.

Custom MCP poderá ser adicionado após a base: registrar o servidor público no Composio, esperar o
sync, adicioná-lo como toolkit `CUSTOM_*` na mesma allowlist e exigir seleção explícita de conta
quando houver autenticação. Como é experimental, não entra no piloto.

## Fontes consultadas

- [Sessions via MCP — Composio](https://docs.composio.dev/docs/sessions-via-mcp)
- [Sessions e isolamento por usuário — Composio](https://docs.composio.dev/docs/how-composio-works)
- [Configuração e allowlists de sessões — Composio](https://docs.composio.dev/docs/configuring-sessions)
- [Autenticação e ciclo de contas conectadas — Composio](https://docs.composio.dev/docs/authentication)
- [Custom MCP — Composio](https://docs.composio.dev/docs/extending-sessions/custom-mcp)
