# API e casos de uso do MVP

## Convenções

Toda chamada é autenticada e limitada ao usuário/workspace do solicitante. As respostas retornam objetos de domínio, evidências e estado de processamento; detalhes de apresentação pertencem à UI.

## Casos de uso essenciais

| Caso | Entrada | Saída |
| --- | --- | --- |
| `IngestTranscript` | `Transcript Event` | source id, estado e confirmação idempotente. |
| `AskMemory` | pergunta e contexto opcional | resposta, evidências, incertezas e fontes. |
| `SearchMemory` | texto e filtros | entidades, fatos, compromissos e fontes relevantes. |
| `GetEntity` | id de entidade | visão atual, relações e histórico. |
| `CorrectMemory` | alvo, correção e motivo opcional | alteração auditável e projeção atualizada. |
| `DeleteMemory` | alvo e escopo | confirmação de exclusão e trabalho derivado quando necessário. |
| `ProcessAgentTurn` | mensagem, id externo e conversa opcional | resposta, tools e confirmação pendente. |
| `GetOrchestrationTask` | id da tarefa | estado e resultado seguro da própria tarefa. |
| `BindTelegramAccount` | usuário autenticado | deep link temporário para prova de posse. |

## Rotas implementadas

```text
POST   /v1/transcripts
GET    /v1/sources/{id}
DELETE /v1/sources/{id}
POST   /v1/memory/ask
GET    /v1/memory/search
GET    /v1/entities/{id}
POST   /v1/memory/corrections
DELETE /v1/memory/{id}
POST   /v1/agent/turns
GET    /v1/orchestration/tasks/{id}
POST   /v1/channels/telegram/accounts
POST   /webhooks/telegram
POST   /v1/channels/whatsapp/accounts
GET    /webhooks/whatsapp
POST   /webhooks/whatsapp
```

`/webhooks/telegram` exige `X-Telegram-Bot-Api-Secret-Token`. As rotas WhatsApp continuam
disponíveis como adaptador inativo e validam o contrato da Meta.

O webhook aceita mensagens privadas de texto e de voz. Voz cria uma mensagem com estado
`transcribing` e um trabalho durável; após a transcrição, a mesma mensagem passa a
`received` e percorre o agente conversacional normalmente. Consulte
[Voz no Telegram com AssemblyAI](14-telegram-voice-transcription.md).

O Bot API usa a implementação publicada como Supabase Edge Function:

```text
https://onuxlluzwlnkhbsfiind.supabase.co/functions/v1/telegram-webhook
```

As rotas FastAPI permanecem disponíveis para desenvolvimento e testes locais.

## Contratos formais mínimos

### `POST /v1/transcripts`

```json
{
  "capture_id": "uuid",
  "source": "iphone",
  "captured_at": "ISO-8601 com fuso",
  "transcript": "texto não vazio",
  "duration_seconds": 0,
  "language": "pt-BR",
  "metadata": {}
}
```

Retorna `201` na primeira persistência e `200` em repetição idempotente:

```json
{ "source_id": "uuid", "status": "received", "idempotent_replay": false }
```

### `POST /v1/memory/ask`

```json
{ "question": "O que ficou pendente com Carlos?", "context": { "entity_ids": [], "from": null, "to": null } }
```

Retorna `200` com `answer`, `evidence[]`, `uncertainties[]` e `source_ids[]`. Cada item de `evidence` contém `source_id`, `excerpt` e, quando aplicável, o `fact_id` que sustenta a resposta.

### Busca, entidade e correção

- `GET /v1/memory/search?q=&entity_id=&type=&status=&from=&to=&cursor=` retorna `items`, `next_cursor` e `total` opcional.
- `GET /v1/entities/{id}` retorna entidade, fatos atuais, relações e histórico paginado.
- `POST /v1/memory/corrections` recebe `{ "target_id", "operation", "value", "reason" }`, onde `operation` é `replace`, `dispute` ou `delete`.

### `POST /v1/agent/turns`

```json
{
  "message_id": "client-message-001",
  "message": "Quais são minhas pendências?",
  "conversation_id": null
}
```

`message_id` é obrigatório e idempotente por workspace. A primeira resposta cria uma conversa; os
turnos seguintes reutilizam o `conversation_id` retornado. A saída informa resposta, `tools_used`,
uma possível `pending_action`, `orchestration_task_id` e replay. Quando há delegação, a resposta é o
acknowledgment; o resultado chega posteriormente no canal ou pode ser consultado por
`GET /v1/orchestration/tasks/{task_id}`.

### Vínculo do Telegram

`POST /v1/channels/telegram/accounts` recebe `display_name` opcional. O vínculo começa inativo e
retorna um `verification_deep_link` com validade de 15 minutos. A conta só é ativada quando o link
abre o bot e o Telegram envia `/start <código>` pelo chat privado. Chats desconhecidos ou inativos
não são encaminhados ao agente.

## Status HTTP e erros

| Status | Uso |
| --- | --- |
| `200` | Consulta bem-sucedida ou replay idempotente. |
| `201` | Fonte criada. |
| `202` | Webhook aceito e persistido para processamento assíncrono. |
| `400` | Contrato inválido. |
| `401` / `403` | Identidade ausente ou acesso fora do workspace. |
| `404` | Recurso inexistente ou indisponível para o workspace. |
| `409` | Conflito de idempotência ou versão incompatível. |
| `429` | Limite de uso excedido. |
| `500` | Falha inesperada sem detalhes sensíveis. |

Erros usam o formato `{ "error": { "code", "message", "request_id" } }`. `message` é seguro para o cliente; detalhes internos ficam apenas na observabilidade.

## Consulta fundamentada

Para responder, o backend identifica filtros explícitos (pessoa, projeto, data e status), busca registros relevantes, seleciona evidências e gera uma resposta limitada a esse contexto. A resposta deve indicar quando uma afirmação é inferência, quando existem versões conflitantes e quando não há evidência suficiente.

## Regras de idempotência e erro

- `POST /v1/transcripts` usa `capture_id` como chave de idempotência por workspace.
- Jobs possuem chave própria por fonte e versão do pipeline.
- Erros temporários são repetíveis; erros de contrato retornam detalhes para correção no cliente.
- O cliente recebe o identificador da fonte antes da extração terminar e pode consultar seu estado.
- Mensagens usam a chave única `(provider, external_message_id)`; replays retornam ou reutilizam o
  resultado persistido.
- Cada tool possui idempotência por turno, nome, versão e hash dos argumentos.
- A resposta ao Telegram é criada na outbox e repetida pelo worker sem gerar outra resposta lógica.

## Fluxo de correção

```text
Usuário seleciona informação → UI envia correção → domínio valida
→ registra auditoria → atualiza projeções → resposta e busca refletem a nova versão
```

O usuário não edita diretamente tabelas de memória nem precisa compreender a estrutura interna.

## Uso dos casos de uso como tools

Os casos de uso estão adaptados como tools do agente conversacional. Uma tool não é uma chamada
HTTP arbitrária: é um adaptador tipado que recebe argumentos validados e chama diretamente o
serviço de aplicação correspondente.

O executor injeta `RequestContext` a partir da identidade vinculada ao canal. `user_id`,
`workspace_id`, credenciais e políticas de autorização nunca são argumentos fornecidos pelo modelo.
Tools de leitura executam automaticamente; mutações recebem idempotência e política de risco;
exclusões exigem uma confirmação posterior do usuário. Consulte
[08-whatsapp-agent-tools-plan.md](08-whatsapp-agent-tools-plan.md).
