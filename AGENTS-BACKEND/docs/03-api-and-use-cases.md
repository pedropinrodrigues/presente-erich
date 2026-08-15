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

## Rotas sugeridas

```text
POST   /v1/transcripts
GET    /v1/sources/{id}
POST   /v1/memory/ask
GET    /v1/memory/search
GET    /v1/entities/{id}
POST   /v1/memory/corrections
DELETE /v1/memory/{id}
```

Os nomes podem mudar; os contratos e comportamentos devem permanecer estáveis para os clientes.

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

## Status HTTP e erros

| Status | Uso |
| --- | --- |
| `200` | Consulta bem-sucedida ou replay idempotente. |
| `201` | Fonte criada. |
| `202` | Correção ou exclusão aceita com processamento derivado pendente. |
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

## Fluxo de correção

```text
Usuário seleciona informação → UI envia correção → domínio valida
→ registra auditoria → atualiza projeções → resposta e busca refletem a nova versão
```

O usuário não edita diretamente tabelas de memória nem precisa compreender a estrutura interna.
