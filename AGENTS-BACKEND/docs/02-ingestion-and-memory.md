# Ingestão e memória

## Entrada

O backend recebe o `Transcript Event` definido em `CAPTURE-INGESTION`. `capture_id` é a chave de idempotência; o mesmo identificador deve retornar a confirmação da fonte já criada, sem reiniciar o processamento.

```json
{
  "capture_id": "uuid",
  "source": "iphone",
  "captured_at": "2026-08-13T10:15:00-03:00",
  "transcript": "Conversei com Carlos sobre o Projeto Alfa...",
  "metadata": {}
}
```

## Pipeline

```text
validar contrato → persistir fonte → criar job → confirmar recebimento
→ extrair candidatos → validar domínio → consolidar memória → indexar
```

A confirmação de recebimento ocorre após a fonte estar persistida. Extração e indexação podem falhar e ser repetidas sem exigir novo envio do dispositivo.

## Dados mínimos

| Registro | Finalidade | Campos essenciais |
| --- | --- | --- |
| Source | Evidência original | id, workspace, capture_id, texto, origem, data, status. |
| Entity | Pessoa, organização ou projeto | id, tipo, nome canônico, aliases, status. |
| Fact | Informação declarada ou inferida | sujeito, predicado, valor, status, confiança, tempo. |
| Commitment | Pendência ou responsabilidade | responsável, descrição, prazo, status, evidências. |
| Evidence | Ligação à fonte | source_id, trecho, posição quando disponível. |
| Job | Processamento recuperável | tipo, chave, status, tentativas, erro. |
| Audit event | Histórico de alterações | ator, operação, alvo, data, motivo. |

## Extração e validação

O extrator retorna candidatos tipados, sempre ligados a trechos da fonte. Exemplos: pessoa mencionada, decisão, compromisso, prazo ou mudança de cargo. Antes de persistir, o domínio valida schema, evidência, escopo do workspace e regras temporais.

Entity resolution deve começar de modo conservador: vincular somente quando houver sinal suficiente; em caso de dúvida, criar candidato para revisão ou manter entidades separadas. Não é necessário um motor sofisticado de grafo no MVP.

## Temporalidade e correções

Um fato possui estado (`current`, `superseded`, `disputed` ou `deleted`), período de validade quando conhecido e uma ou mais evidências. Ao identificar atualização confiável, o fato anterior passa a `superseded`; ele não é apagado. Correções do usuário criam uma operação auditável e atualizam a projeção atual.

## Estados de domínio

| Registro | Estados | Transições permitidas |
| --- | --- | --- |
| Source | `received`, `processing`, `processed`, `failed`, `deleted` | `received → processing → processed`; falha retorna a `received` para retry ou vira `failed`; exclusão leva a `deleted`. |
| Job | `queued`, `running`, `retrying`, `completed`, `failed` | `queued → running → completed`; falha transitória vira `retrying`; falha sem retry vira `failed`. |
| Fact | `proposed`, `current`, `superseded`, `disputed`, `deleted` | validação promove `proposed` a `current`; atualização torna o anterior `superseded`; revisão humana pode marcar `disputed` ou `deleted`. |
| Commitment | `open`, `completed`, `cancelled`, `deleted` | é criado como `open`; usuário ou evidência posterior pode concluí-lo ou cancelá-lo. |

Transições são aplicadas por serviços de domínio e registram um evento de auditoria. O modelo de IA não altera estado diretamente.

## Reprocessamento e exclusão

Reprocessar uma fonte usa a transcrição persistida e uma versão explícita do extrator. Antes de aplicar o resultado, o domínio deduplica operações já realizadas.

Ao excluir uma fonte ou memória, aplicar a política de retenção do workspace, apagar ou anonimizar projeções derivadas necessárias e registrar o evento de exclusão. A resposta posterior não pode recuperar material removido.

## Retenção e exclusão

- Fontes, memórias e auditorias ativas são mantidas até exclusão pelo `owner`; não há expiração automática no MVP.
- Uma exclusão torna o conteúdo indisponível imediatamente para API, busca, embeddings e jobs futuros.
- Projeções derivadas, índices e embeddings associados devem ser removidos no mesmo fluxo lógico de exclusão.
- O registro de auditoria mantém somente identificadores, ator, data, tipo de operação e motivo; nunca preserva a transcrição excluída.
- Backups seguem a retenção padrão da plataforma por até 30 dias; restaurações não podem reativar registros marcados como excluídos.

## Governança do modelo de IA

O model gateway usa o SDK Python oficial da OpenAI e recebe apenas a fonte e o contexto mínimo necessários ao caso de uso. Toda saída de extração deve obedecer a JSON Schema versionado, com campos de evidência obrigatórios e `confidence` entre `0` e `1`. A chamada usa Structured Outputs e `store=false`.

- A versão de modelo, prompt e schema é gravada em cada execução.
- Extração usa temperatura `0` no MVP para reduzir variação.
- Saída inválida recebe no máximo uma nova tentativa com instrução de correção; depois o job falha de forma observável.
- Conteúdo da transcrição é dado não confiável, nunca instrução para o sistema ou para ferramentas.
- Candidatos com `confidence < 0.70` permanecem `proposed` e não mudam a visão atual sem revisão humana ou evidência posterior compatível.
- LangGraph e LangChain não participam do MVP por padrão; a extração é um workflow Python explícito e testável.
