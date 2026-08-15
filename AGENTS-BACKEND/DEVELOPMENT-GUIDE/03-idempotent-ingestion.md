# Etapa 03 — Ingestão idempotente

## Objetivo

Receber uma transcrição com segurança e confirmar ao cliente somente depois que a fonte estiver duravelmente salva. A extração não ocorre dentro da requisição: ela é responsabilidade de um job recuperável.

## Pré-requisitos e acessos

- Etapa 02 aprovada.
- Banco de desenvolvimento disponível para testes de integração.
- Contrato de `Transcript Event` acordado com Capture & Ingestion.

## Implementação, na ordem

1. Validar o corpo de `POST /v1/transcripts` contra o contrato publicado.
2. Normalizar campos técnicos sem alterar o texto original da transcrição.
3. Criar `Source` com `capture_id` único por workspace.
4. Criar `Job` de extração associado à fonte, usando chave idempotente por fonte e versão de pipeline.
5. Retornar `201` para a primeira gravação e `200` para replay do mesmo `capture_id`.
6. Implementar worker para reservar job, processá-lo, registrar tentativas e finalizar estado.
7. Expor leitura de estado da fonte apenas ao owner do workspace.

## Entregáveis

- Rota autenticada de ingestão.
- Persistência de fonte original e job.
- Worker local com retry limitado e erros normalizados.
- Telemetria correlacionando `request_id`, `source_id` e `job_id`.

## Testes obrigatórios

- Primeiro envio gera uma única `Source` e retorna `201`.
- Reenvio idêntico retorna `200`, mesmo `source_id` e nenhum job extra.
- Reenvio com o mesmo `capture_id` e conteúdo incompatível retorna `409`.
- Payload inválido retorna `400` sem dados parciais.
- Falha transitória gera retry; limite de tentativas leva a `failed` com diagnóstico seguro.
- Worker de um workspace não processa fonte em outro contexto indevido.

## Gate de pronto

- Cliente pode reenviar uma captura após queda de rede sem duplicar dados.
- O backend não recebe nem persiste áudio.
- A fonte sobrevive a reinício do processo e pode ser processada posteriormente.

## Não fazer nesta etapa

- Interpretar semanticamente a transcrição na rota HTTP.
- Confirmar recebimento antes da persistência da fonte.
- Expor transcrição a logs ou mensagens de erro.
