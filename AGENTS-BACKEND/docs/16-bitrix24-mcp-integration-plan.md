# Plano de integração Bitrix24 MCP

> Estado em 01/09/2026: caminho por token implementado no backend. A ativação em produção ainda
> depende de aplicar a migration, configurar os slugs retornados por `list_tools` em um tenant
> Bitrix24 de teste e habilitar as variáveis do ambiente. OAuth continua como evolução posterior.

## Objetivo

Permitir que o orquestrador consulte e, após confirmação explícita, altere CRM e tarefas do
Bitrix24 sem entregar ao Luna, ao Terra ou ao canal credenciais do workspace. A integração deve
preservar as garantias já usadas pelo gateway Composio:

- isolamento por usuário e workspace;
- catálogo de tools limitado por política local;
- leitura R0 e escrita externa R2;
- confirmação em turno posterior para qualquer efeito;
- idempotência, auditoria, sanitização e reconciliação;
- nenhuma credencial em prompts, logs, eventos ou mensagens;
- falha incerta nunca tratada como sucesso.

O piloto cobre negócios do CRM e tarefas. Agenda, arquivos, chats, estrutura organizacional,
convites e e-mail ficam fora do primeiro catálogo.

## Dois servidores Bitrix24 distintos

### MCP Dev Server

```text
https://mcp-dev.bitrix24.com/mcp
```

É público, não exige autenticação e fornece documentação REST estruturada: métodos, parâmetros,
valores permitidos e orientações. Serve para desenvolver e revisar a integração; não acessa o
workspace do cliente nem executa operações nele. O transporte é MCP Streamable HTTP, sem SSE.

### MCP operacional

```text
https://mcp.bitrix24.com/mcp/
```

É autenticado por OAuth ou token de conexão e opera dados do workspace dentro das permissões do
funcionário. O administrador do Bitrix24 precisa habilitar conexões externas em
`Configurações > Servidores MCP`. Disponibilidade e catálogo podem variar conforme plano,
permissões e evolução do produto.

O backend nunca deve confundir o Dev Server com uma integração operacional. O endpoint produtivo é
fixo em configuração e não pode ser informado pelo usuário ou pelo modelo.

## Decisão arquitetural

Implementar um cliente MCP no backend e expor ao Terra somente function tools locais estáveis.
Não conectar o MCP operacional diretamente à Responses API no piloto.

```text
Telegram / HTTP
        │
        ▼
Luna decide delegate
        │ OrchestrationTask persistida
        ▼
Terra recebe tools Bitrix locais permitidas
        │ function call estrita
        ▼
ToolRegistry + política R0/R2
        │
        ▼
BitrixMcpGateway
        │ MCP Streamable HTTP autenticado
        ▼
https://mcp.bitrix24.com/mcp/
        │
        ├─ CRM
        └─ tarefas
```

Embora a Responses API aceite uma tool `mcp` com `server_url`, `authorization`, `allowed_tools` e
`require_approval`, esse caminho criaria um segundo mecanismo de aprovação e permitiria que a
chamada externa ocorresse fora do `ToolRegistry`, `ExternalAction` e `PendingAction`. O gateway
local mantém uma única fronteira de autorização, auditoria e resultado.

O MCP Dev Server pode ser conectado ao ambiente de desenvolvimento para consultar documentação,
mas não deve fazer parte do runtime de produção.

## Autenticação e ciclo da conexão

### Spike: token manual

O primeiro spike usa um token criado pelo funcionário em
`Aplicações > Conexões MCP > Obter token de conexão`.

1. Administrador habilita MCP no tenant de teste.
2. Funcionário cria um token revogável.
3. O backend gera um link HTTPS privado e temporário; o estado opaco fica no fragmento da URL e
   não é enviado em access logs.
4. A página envia estado e token no corpo JSON diretamente ao backend; o token nunca passa pelo
   Telegram, pela OpenAI ou pela URL.
5. O gateway executa `initialize` e `list_tools`; somente depois disso o backend cifra o token.
6. A página orienta o usuário a voltar ao canal e enviar `confirmo` em uma nova mensagem.
7. `PendingAction` ativa a integração; cancelamento ou revogação apaga a credencial local.
8. Falha de autorização marca a integração como `authorization_failed`.

Não aceitar token pelo Telegram. A implementação é multiusuário e armazena uma credencial cifrada
por conexão em `external_integrations.credential_ciphertext`.

### Produto: OAuth

OAuth substitui a entrada manual depois que o spike comprovar o catálogo real. O fluxo esperado é:

```text
usuário pede conexão
  → backend cria ExternalConnectionRequest
  → outbox entrega URL de autorização
  → Bitrix24 autentica o funcionário
  → callback valida state, usuário, workspace e expiração
  → tokens são criptografados
  → conexão é verificada por MCP
  → ExternalIntegration fica active
```

Antes de implementar, validar em tenant de teste:

- descoberta do authorization server;
- registro de client, se dinâmico ou previamente cadastrado;
- redirect URIs aceitas;
- presença e rotação de refresh token;
- escopos e duração do access token;
- revogação e reconexão;
- suporte a múltiplos portais pelo mesmo usuário.

## Segredos

Criar uma chave exclusiva, por exemplo `EXTERNAL_CREDENTIAL_ENCRYPTION_KEY`, em vez de reutilizar
`COMPOSIO_USER_ID_SECRET`. O token deve ser cifrado com AEAD/Fernet e descriptografado somente no
momento de abrir a sessão MCP.

Nunca persistir ou registrar:

- token em claro, hash reversível, header `Authorization` ou URL com credencial;
- payload MCP bruto que possa conter segredo;
- token em `integration_metadata`, `ToolExecution` ou `OrchestrationTaskEvent`;
- credenciais em mensagens de exceção.

Logs HTTP para `mcp.bitrix24.com` devem redigir headers e query strings. O token não é enviado à
OpenAI na arquitetura escolhida.

## Modelo de dados

As tabelas atuais podem ser reaproveitadas, mas campos específicos do Composio precisam ser
generalizados antes do MVP.

### `external_integrations`

Adicionar ou renomear:

```text
provider = "bitrix24"
toolkit_slug = "bitrix24"
connected_account_id = identidade estável do portal + funcionário
provider_session_id nullable
credential_ciphertext nullable
credential_kind = "connection_token" | "oauth"
credential_expires_at nullable
portal_url_sanitized
remote_user_id nullable
status = pending | active | expired | revoked | failed
last_verified_at, revoked_at
```

`credential_ciphertext` não pode aparecer em serializers, respostas ou eventos. Se for preferível
separar responsabilidades, criar `external_credentials` com FK para `external_integrations` e
permissão de leitura restrita ao worker.

### `external_actions`

Generalizar `composio_execution_id` para `provider_execution_id`. Os demais campos já representam
o fluxo necessário:

```text
provider = "bitrix24"
toolkit_slug = "bitrix24"
tool_slug = nome remoto descoberto
risk_level, arguments_sanitized, arguments_ciphertext
arguments_hash, idempotency_key
status = proposed | confirmed | executing | succeeded |
         failed | outcome_unknown | cancelled
provider_execution_id nullable
result_sanitized nullable
```

## Componentes propostos

```text
src/agents_backend/integrations/bitrix24/
  __init__.py
  gateway.py          transporte MCP, discovery e chamada
  policies.py         allowlist, risco, schemas e redaction
  service.py          conexão, leitura, proposta e execução
  results.py          normalização e limites de resultados

tests/
  test_bitrix24_gateway.py
  test_bitrix24_integration.py
```

### `BitrixMcpGateway`

Responsabilidades:

- usar somente `https://mcp.bitrix24.com/mcp/`;
- configurar timeout total e de conexão;
- enviar autenticação apenas no cliente HTTP;
- executar `initialize`, `list_tools` e `call_tool` via `mcp.ClientSession`;
- validar que a tool remota está na allowlist antes da chamada;
- mapear erros de autenticação, protocolo, timeout e execução;
- não repetir escrita automaticamente;
- devolver um payload estruturado, nunca objetos do SDK.

Interface inicial:

```python
class BitrixMcpGateway:
    async def list_tools(self, credential: str) -> list[RemoteTool]: ...

    async def execute(
        self,
        *,
        credential: str,
        remote_tool: str,
        arguments: dict[str, object],
    ) -> dict[str, object]: ...
```

Discovery serve para validar compatibilidade no connect/health check. O catálogo do agente é
definido pela política versionada no código, não por tudo que `list_tools` retornar.

## Capacidades e roteamento

Adicionar capacidades específicas, sem liberar Bitrix globalmente:

```text
bitrix_connection
bitrix_crm_read
bitrix_task_read
bitrix_execute
```

Sugestão de intenções:

| Pedido | Intenção | Capacidades |
| --- | --- | --- |
| conectar/verificar Bitrix24 | `account_management` | `bitrix_connection` |
| consultar negócio | `external_communication` | `bitrix_crm_read` |
| consultar tarefa | `automation` | `bitrix_task_read` |
| criar/alterar tarefa ou negócio | `compound` | leitura necessária + `bitrix_execute` |

Se o crescimento do CRM tornar `compound` amplo demais, introduzir posteriormente
`crm_management`. No piloto, as capacidades e o catálogo continuam sendo a autorização efetiva.

## Catálogo inicial

Os nomes remotos precisam ser confirmados por `list_tools` no spike. O Terra vê somente nomes
locais estáveis:

| Tool local | Risco | Resultado esperado |
| --- | --- | --- |
| `bitrix_search_deals` | R0 | negócios compactos com ID, título, estágio, valor e responsável |
| `bitrix_get_deal` | R0 | detalhes permitidos de um negócio |
| `bitrix_list_tasks` | R0 | tarefas compactas, status, prazo e responsável |
| `bitrix_get_task` | R0 | detalhes permitidos de uma tarefa |
| `bitrix_create_task` | R2 | proposta de criação; nunca executa no primeiro turno |
| `bitrix_update_task` | R2 | proposta de alteração por ID |
| `bitrix_update_deal` | R2 | proposta de alteração de campos/estágio por ID |

Não expor ao modelo:

- tool MCP genérica `execute`;
- `list_tools` ou descoberta dinâmica;
- exclusão de negócio/tarefa;
- compra ou alteração de plano;
- administração de usuários, departamentos ou permissões;
- movimentação de arquivo, envio de mensagem ou convite no piloto.

## Fluxos

### Leitura R0

```text
“Quais negócios estão parados na etapa de proposta?”
  → Luna delega
  → Terra recebe bitrix_search_deals
  → serviço resolve conexão ativa do usuário
  → gateway chama somente a tool remota mapeada
  → resultado é normalizado e limitado
  → ToolExecution e ExternalAction são auditados
  → Terra responde no chat
```

Resultados devem trazer semântica de contagem (`exact` ou `at_least`), paginação e indicação de
truncamento. Não enviar payload completo de CRM ao contexto quando poucos campos respondem ao
pedido.

### Escrita R2

```text
“Mova o negócio Acme para negociação”
  → leitura identifica negócio e estágio
  → ExternalAction proposed
  → PendingAction mostra portal, negócio, estado atual e mudança
  → usuário confirma em turno posterior
  → ação passa a executing
  → gateway chama Bitrix24 uma vez
  → succeeded, failed ou outcome_unknown
  → resposta final pela outbox
```

Nunca confiar em título como identificador na execução. A confirmação deve congelar IDs e valores
normalizados, cifrando os argumentos completos.

## Idempotência e reconciliação

Antes de liberar escrita, descobrir se cada tool remota aceita chave idempotente. Na ausência:

- criação: consultar por marcador externo determinístico quando o Bitrix permitir;
- atualização: ler estado atual antes e depois da chamada;
- timeout após envio: marcar `outcome_unknown`, não repetir;
- retry automático: permitido somente antes de enviar bytes ou em leitura R0;
- confirmação repetida: devolver o resultado persistido da mesma `ExternalAction`.

Uma rotina de reconciliação pode reler o alvo por ID para decidir se a alteração foi aplicada.

## Normalização e privacidade

O normalizador deve operar por tool e usar allowlist de campos. Remover:

- campos customizados não solicitados;
- comentários, descrições longas e HTML quando não necessários;
- e-mails, telefones e identificadores pessoais fora do pedido;
- URLs internas, tokens, metadados do portal e blobs;
- payloads aninhados desconhecidos.

Aplicar limites de itens, caracteres por campo e tamanho total antes de devolver o resultado ao
Terra. Conteúdo vindo do Bitrix24 é dado não confiável e pode conter prompt injection em títulos,
descrições, comentários ou arquivos.

## Configuração implementada

```text
BITRIX24_MCP_ENABLED=false
BITRIX24_MCP_URL=https://mcp.bitrix24.com/mcp/
BITRIX24_PUBLIC_BASE_URL=https://api.example.com
BITRIX24_CREDENTIAL_ENCRYPTION_KEY=
BITRIX24_AUTH_SCHEME=bearer
BITRIX24_TIMEOUT_SECONDS=20
BITRIX24_CONNECTION_TTL_SECONDS=600
BITRIX24_CONNECTION_MAX_ATTEMPTS=5
BITRIX24_EXPIRATION_SCAN_INTERVAL_SECONDS=60
BITRIX24_TOOL_SEARCH_DEALS=
BITRIX24_TOOL_GET_DEAL=
BITRIX24_TOOL_UPDATE_DEAL=
BITRIX24_TOOL_LIST_TASKS=
BITRIX24_TOOL_GET_TASK=
BITRIX24_TOOL_CREATE_TASK=
BITRIX24_TOOL_UPDATE_TASK=
```

Em produção, validar que `BITRIX24_MCP_URL` coincide exatamente com o host oficial. Não aceitar
redirect para outro host sem uma política explícita. O validator atual exige a URL oficial exata.
`BITRIX24_AUTH_SCHEME=raw` permite tenants cujo cliente de token exija o valor puro no header
`Authorization`; o default é `Bearer`.

Os sete slugs remotos são deliberadamente vazios por padrão. Após o primeiro `list_tools` no tenant
piloto, preencher apenas os slugs revisados. Uma operação sem slug configurado não é enviada ao
modelo, mesmo que o servidor remoto a anuncie.

## Fases de implementação

### Fase 0 — spike autenticado, somente leitura

- obter tenant e token de teste;
- validar Streamable HTTP, autenticação e TLS;
- registrar `list_tools`, schemas e annotations sem registrar o token;
- identificar nomes remotos para CRM e tarefas;
- testar paginação, limites, erros e revogação;
- produzir fixtures sanitizadas para testes offline.

**Gate:** leitura real de um negócio e uma tarefa sem expor segredo ou escrita.

### Fase 1 — fundação e conexão

- migration para credencial criptografada e nomes genéricos de provider;
- configurações e validações;
- `BitrixMcpGateway` com fake determinístico;
- conectar, verificar, listar e revogar conexão;
- eventos de auditoria sem dados sensíveis.

**Gate:** conexão isolada por workspace/usuário e revogação comprovada.

### Fase 2 — leituras R0

- políticas e schemas locais de negócio/tarefa;
- normalizadores e limites;
- roteamento Luna/Terra;
- tools de busca e detalhe;
- métricas de latência, erro e volume.

**Gate:** consultas chegam pelo Telegram e não expõem tools fora da intenção.

### Fase 3 — escritas R2

- `ExternalAction` + `PendingAction`;
- confirmação em turno posterior;
- idempotência/reconciliação por operação;
- create/update de tarefa e update de negócio;
- tratamento de `outcome_unknown`.

**Gate:** nenhuma escrita ocorre no primeiro turno nem é repetida por replay.

### Fase 4 — OAuth e produção

- substituir token manual pelo fluxo OAuth validado;
- rotação, expiração, revogação e múltiplos portais;
- canário Northflank com conta de teste;
- alertas, runbook e rollout gradual.

**Gate:** operação sem segredo manual e rollback documentado.

## Testes obrigatórios

### Unitários

- endpoint fixo e headers redigidos;
- catálogo remoto fora da allowlist rejeitado;
- schema remoto incompatível rejeitado;
- normalização, paginação e limites;
- `401`, `403`, `429`, timeout e erro MCP;
- escrita não repetida após timeout incerto;
- token ausente de logs, eventos, envelopes e snapshots.

### Integração com fake MCP

- initialize/list/call pelo transporte esperado;
- conexão por workspace e usuário;
- leitura R0 completa;
- proposta, confirmação, execução e replay R2;
- revogação entre proposta e confirmação;
- resultado persistido antes da resposta final.

### Canário real

- listar uma tarefa conhecida;
- localizar um negócio conhecido;
- criar uma tarefa identificada como canário após confirmação;
- alterar e restaurar um campo não destrutivo;
- verificar auditoria e ausência de duplicata;
- revogar token e comprovar bloqueio imediato.

## Observabilidade

Registrar sem payload sensível:

- provider, tool local/remota e versão da política;
- integração e portal por IDs internos;
- latência de discovery e execução;
- status, código de erro normalizado e retryability;
- itens recebidos/devolvidos e truncamento;
- ação proposta, confirmada, executada ou incerta;
- última verificação e expiração da conexão.

Alertar para aumento de `401/403`, `429`, timeout, mudança de schema e `outcome_unknown`.

## Fora de escopo inicial

- conectar o MCP Bitrix diretamente à Responses API;
- importar todo o catálogo remoto automaticamente;
- rotinas recorrentes com escrita no Bitrix24;
- exclusões e administração de assinatura;
- estrutura organizacional, usuários, convites, arquivos e chats;
- MCP Hub do Bitrix24 para conectar terceiros ao próprio Bitrix;
- suportar servidor Bitrix24 on-premise antes de validar rede e OAuth específicos.

## Questões que o spike deve fechar

1. Quais são os nomes e schemas reais das tools operacionais para CRM e tarefas?
2. As annotations MCP identificam corretamente leitura e escrita?
3. O token representa uma pessoa, portal ou ambos?
4. Há expiração, refresh ou somente revogação do token manual?
5. Quais tools oferecem idempotência ou identificador externo?
6. Como o servidor representa paginação, rate limit e erros de negócio?
7. O endpoint suporta múltiplos portais com a mesma credencial?
8. O plano do tenant piloto inclui MCP operacional e REST necessários?

## Critérios de aceite do MVP

- somente tenant, usuário e workspace conectados podem ser consultados;
- Terra recebe exclusivamente tools autorizadas para a intenção;
- toda escrita exige confirmação explícita posterior;
- token não aparece fora do armazenamento cifrado e memória transitória do gateway;
- replay não repete efeitos;
- timeout pós-envio não dispara retry de escrita;
- resultados são limitados e sanitizados por schema local;
- revogação bloqueia novas chamadas;
- API, worker, filas e outbox permanecem saudáveis no rollout;
- documentação e catálogo registram a revisão dos schemas remotos validados.

## Referências oficiais

- [Bitrix24 MCP operacional e autenticação](https://helpdesk.bitrix24.com/open/25866707/)
- [Bitrix24 MCP Dev Server](https://apidocs.bitrix24.com/ai-tools/mcp.html)
- [Acesso à REST API do Bitrix24](https://apidocs.bitrix24.com/first-steps/access-to-rest-api.html)
- [OpenAI Docs — MCP e Connectors](https://developers.openai.com/api/docs/guides/tools-connectors-mcp)
- [OpenAI Responses API](https://developers.openai.com/api/reference/cli/resources/responses/methods/create)
