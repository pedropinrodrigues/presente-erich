# Agente conversacional, tools e canais de mensagem

## Estado da implementação

As etapas de persistência, tools, runtime, endpoint independente de canal e adaptador textual do
Telegram estão implementadas. O vínculo usa deep link temporário, a Supabase Edge Function persiste
a entrada e o worker processa turnos e outbox. O adaptador Meta WhatsApp foi preservado, mas está
inativo por configuração durante o piloto.

O catálogo originalmente único foi posteriormente separado: o agente rápido mantém apenas leitura
e delegação, enquanto mutações são processadas pelo orquestrador persistido descrito em
[09-orchestrated-agent-architecture.md](09-orchestrated-agent-architecture.md).

## Decisão de produto

O Telegram é a interface principal do piloto. O usuário não precisa conhecer rotas, IDs internos ou a
estrutura da memória. Um agente conversacional interpretará mensagens como “quais são minhas
pendências?”, “corrija a data do Atlas” ou “apague essa informação”, invocará casos de uso do
backend como tools e devolverá o resultado no mesmo canal.

Esta camada não substitui o domínio existente. A IA escolhe entre operações permitidas; o backend
continua responsável por identidade, autorização, validação, confirmação, idempotência, auditoria e
persistência.

## Arquitetura implementada

```text
Telegram ──→ Supabase Edge Function ──→ mensagem externa idempotente
                                             │
Cliente HTTP ──→ FastAPI                     ▼
                              Adaptador de canal ──→ vínculo canal ↔ usuário/workspace
          │
          ▼
Conversation Service ──→ histórico local e estado da conversa
          │
          ▼
Conversation Agent (Responses API)
          │ function_call
          ▼
Tool Registry ──→ Policy / Confirmation / Idempotency
          │
          ▼
Serviços de aplicação existentes
  ├─ ingestion
  ├─ retrieval
  └─ memory mutations
          │
          ▼
Supabase / PostgreSQL
          │ function_call_output
          ▼
Conversation Agent ──→ resposta final ──→ Outbox ──→ Telegram
```

O extrator assíncrono continua separado. O novo agente conversacional seleciona tools e formula o
feedback ao usuário; ele não recebe SQL, credenciais, acesso genérico à rede ou uma tool HTTP
arbitrária.

## Catálogo inicial de tools

### Leitura — execução automática

| Tool | Caso de uso interno | Resultado mínimo |
| --- | --- | --- |
| `search_memory` | `SearchMemory` | itens, status, IDs opacos e evidências resumidas |
| `get_entity` | `GetEntity` | entidade, fatos atuais, compromissos e histórico |
| `get_source_status` | `GetSource` | estado do processamento e metadados seguros |
| `list_open_commitments` | busca filtrada | pendências abertas, responsáveis, prazos e evidências |
| `get_pending_action` | nova camada de confirmação | operação aguardando confirmação e expiração |

O agente responde perguntas usando os resultados dessas tools. A implementação evita chamar o
agente respondedor atual dentro do novo agente quando uma única etapa de retrieval já fornecer
evidência suficiente; isso reduz custo e elimina um loop de modelos desnecessário.

### Escrita — controlada por política

| Tool | Caso de uso interno | Regra inicial |
| --- | --- | --- |
| `remember_transcript` | `IngestTranscript` | somente quando o usuário pedir explicitamente para guardar |
| `correct_memory` | `CorrectMemory` | localizar o alvo, apresentar resumo e confirmar quando houver ambiguidade |
| `dispute_memory` | `CorrectMemory` | pode executar após intenção explícita e alvo inequívoco |
| `delete_memory` | `DeleteMemory` | sempre criar confirmação pendente; nunca excluir no primeiro turno |
| `delete_source` | `DeleteSource` | sempre criar confirmação pendente; mostrar impacto antes de confirmar |
| `confirm_action` | executor de ação pendente | exige mesmo usuário, conversa, token e ação não expirada |
| `cancel_action` | executor de ação pendente | cancela sem produzir efeito de domínio |

Envio de mensagens, e-mails, calendário e automações externas não entram nesse primeiro catálogo.
Quando forem adicionados, exigirão preview e confirmação próprios.

## Contratos e execução de tools

- Cada tool terá JSON Schema com `strict: true`, todos os campos obrigatórios e
  `additionalProperties: false`. Campos opcionais serão obrigatórios, porém anuláveis.
- O registry manterá nome, versão, descrição, schema, nível de risco, handler e serializador seguro.
- O modelo nunca fornecerá `user_id`, `workspace_id`, credencial, papel ou chave de idempotência.
- O executor criará `RequestContext` usando o vínculo autenticado do canal.
- Resultados usarão envelopes estáveis: `ok`, `code`, `message`, `data`, `evidence` e `retryable`.
- Erros de domínio serão devolvidos ao agente como dados seguros, sem stack trace ou segredo.
- Inicialmente, `parallel_tool_calls=false` e haverá no máximo uma tool por passo. Paralelismo poderá
  ser liberado depois apenas para leituras independentes.
- Cada turno terá limite de passos, chamadas, tempo e tokens. Ao atingir o limite, o agente encerra
  com uma falha clara e não repete efeitos já concluídos.

O ciclo será explícito: enviar mensagem e tools à Responses API, receber `function_call`, validar e
executar no backend, devolver `function_call_output` com o mesmo `call_id` e repetir até obter a
mensagem final.

## Identidade, conversas e persistência

Tabelas implementadas pela migration `20260816_0002`:

| Tabela | Finalidade |
| --- | --- |
| `channel_accounts` | vínculo verificado entre provedor, identificador externo e usuário/workspace |
| `conversations` | conversa por canal e usuário, com estado e timestamps |
| `channel_messages` | mensagens recebidas/enviadas, ID externo único, direção e status |
| `agent_runs` | modelo, prompt, tokens, duração, resultado e erro por turno |
| `tool_executions` | nome/versão, argumentos saneados, resultado, risco, status e `call_id` |
| `pending_actions` | mutação proposta, resumo, expiração, confirmação e executor |
| `outbox_messages` | envio confiável ao canal com retry e deduplicação |

O histórico conversacional ficará no nosso banco. As chamadas continuarão com `store=false`; o
backend montará somente a janela recente e o contexto necessário. IDs externos do canal serão
únicos para que retries do webhook não executem o turno nem a mutação novamente.

## Política de risco e confirmação

| Nível | Exemplos | Política |
| --- | --- | --- |
| R0 — leitura | buscar, listar, acompanhar | execução automática |
| R1 — escrita reversível | contestar, correção inequívoca | executar com intenção explícita; confirmar se houver ambiguidade |
| R2 — destrutiva | excluir memória ou fonte | confirmação obrigatória em segundo turno |
| R3 — ação externa futura | enviar mensagem/e-mail, alterar calendário | fora desta fase; preview e confirmação obrigatórios |

“Confirmo” só vale para uma `pending_action` específica da mesma conversa e usuário. A confirmação
expira, não pode ser reutilizada e é consumida atomicamente junto com a mutação. O token aleatório
fica interno; o modelo recebe no máximo o ID opaco, o resumo e a expiração da ação.

## Idempotência e auditoria

- Entrada: chave única `(provider, external_message_id)`.
- Tool: chave derivada de mensagem, `call_id`, nome/versão e hash dos argumentos normalizados.
- Mutação: a execução e o consumo da confirmação ocorrem em transação.
- Saída: outbox com chave própria; retry do provedor não gera nova resposta lógica.
- Auditoria: registrar quem pediu, qual tool foi proposta/executada, confirmação, alvo e resultado,
  sem armazenar segredos ou payloads integrais desnecessários.

## Etapas de implementação

As etapas 1–5 abaixo estão concluídas no código. A etapa 6 possui suíte automatizada e avaliação
sintética segura. O relatório com chamadas reais deve ser reexecutado sempre que prompt, modelo ou
catálogo de tools mudar.

### 1. Preparar os serviços para tools

- Extrair contratos de aplicação estáveis para leitura e mutação.
- Criar `ToolContext`, envelopes de resultado e erros tipados.
- Implementar registry e adaptadores sem OpenAI nem WhatsApp.
- Garantir que todos os handlers injetem o workspace autenticado.

**Gate:** tools podem ser chamadas diretamente em testes e reproduzem o comportamento das rotas.

### 2. Persistir conversas, execuções e confirmações

- Criar migrations e modelos das tabelas novas.
- Implementar idempotência de mensagem/tool e outbox.
- Implementar política R0–R2 e máquina de estados de `pending_actions`.

**Gate:** replay não repete mutação e exclusão sem confirmação é impossível pelo domínio.

### 3. Implementar o runtime do agente

- Adicionar `ConversationAgent` ao model gateway com prompt e versão próprios.
- Registrar as function tools estritas no Responses API.
- Implementar loop `function_call`/`function_call_output`, limites e feedback final.
- Disponibilizar apenas a subset de tools válida para o estado do turno.

**Gate:** cenários de leitura, correção, confirmação e erro funcionam sem canal real.

### 4. Criar uma entrada independente de canal

- Expor endpoint autenticado de teste, por exemplo `POST /v1/agent/turns`.
- Aceitar uma mensagem e retornar resposta, tools usadas e ação pendente quando aplicável.
- Usar o mesmo runtime chamado pelos canais de mensagem.

**Gate:** frontend/CLI consegue usar toda a experiência conversacional sem acessar rotas de memória.

### 5. Integrar um canal de mensagem

- Implementar adaptador do provedor, verificação do webhook e normalização de mensagens.
- Vincular número verificado a uma conta existente sem confiar em IDs recebidos do modelo.
- Processar webhook rapidamente, enfileirar turno e enviar resposta pela outbox.
- Suportar texto primeiro; mídia/áudio entram depois por adaptador de transcrição.

**Gate:** mensagem duplicada do provedor produz uma única execução e uma única resposta lógica.

### 6. Avaliar e liberar

- Criar dataset de intenções, tool esperada, argumentos, confirmações e resposta final.
- Executar testes de prompt injection, alvo ambíguo, cross-workspace e retries.
- Medir acerto de seleção/argumentos, fundamentação, custo, latência e side effects indevidos.

**Gate:** nenhum acesso entre workspaces, nenhuma exclusão sem confirmação e nenhuma mutação
duplicada; respostas e escolhas de tools atingem os gates definidos antes do piloto.

## Testes e gates

- pergunta simples seleciona somente tool de leitura e cita evidência;
- “acompanhe minhas pendências” lista somente compromissos do workspace correto;
- correção ambígua pede esclarecimento em vez de escolher alvo;
- exclusão cria ação pendente e não altera dados no primeiro turno;
- confirmação de outro usuário, conversa ou ação expirada falha;
- replay do webhook e replay do `call_id` não repetem efeitos;
- prompt injection em mensagem ou memória não altera tools permitidas;
- tool desconhecida, argumento inválido e limite de passos encerram com erro seguro;
- falha transitória preserva idempotência e pode ser repetida;
- feedback final informa claramente sucesso, falha, incerteza ou confirmação necessária.

A suíte local cobre contratos estritos, loop e `call_id`, replay de runtime, exclusão em segundo
turno, consumo de confirmação, idempotência da entrada HTTP, segredo/vínculo/replay do Telegram
e todos os testes anteriores de isolamento e domínio. `evaluation/synthetic-agent-turns.jsonl`
cobre seleção de tools, negativa de exclusão, prompt injection e capacidades ainda indisponíveis.

## Mapa do código

| Componente | Local |
| --- | --- |
| Runtime e prompt | `src/agents_backend/conversation/runtime.py` |
| Registry, schemas e políticas | `src/agents_backend/conversation/tools.py` |
| Conversas e idempotência | `src/agents_backend/conversation/service.py` |
| Telegram e vínculo | `src/agents_backend/conversation/telegram.py` |
| Jobs e outbox multicanal | `src/agents_backend/conversation/channel_jobs.py` |
| Gateway público do Telegram | `supabase/functions/telegram-webhook/` |
| WhatsApp preservado | `src/agents_backend/conversation/whatsapp.py` |
| Rotas | `src/agents_backend/api/routes.py` e `api/telegram_routes.py` |
| Migration | `migrations/versions/20260816_0002_conversation_agent.py` |
| Avaliação segura | `scripts/evaluate_conversation.py` |

## Métricas propostas

- schema válido de tool calls: ≥ 99%;
- seleção correta de tool em casos inequívocos: ≥ 95%;
- argumentos corretos: ≥ 95%;
- respostas fundamentadas para leitura: ≥ 95%;
- mutação duplicada: 0%;
- acesso cross-workspace: 0%;
- ação R2 sem confirmação válida: 0%;
- feedback final coerente com o resultado da tool: ≥ 95%.

## Fora desta implementação

- múltiplos agentes autônomos;
- tool HTTP genérica, SQL ou acesso direto ao Supabase pelo modelo;
- e-mail, calendário e envio proativo;
- memória de conversa hospedada no provedor;
- áudio do WhatsApp antes do fluxo textual estar aprovado;
- LangGraph/LangChain sem necessidade comprovada pelo runtime explícito.
