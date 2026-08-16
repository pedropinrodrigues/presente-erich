# Seleção de modelos e custo

## Decisão atual

Não trocar modelos apenas pelo preço nominal. O MVP escolhe a combinação de menor custo que passe
todos os gates do dataset versionado. O relatório deve registrar modelo, prompt e schema para que a
comparação seja reproduzível.

Configuração do MVP:

- extração: `gpt-5.6-luna`, com `reasoning.effort=none`;
- resposta fundamentada: `gpt-5.6-luna`, com `reasoning.effort=none`;
- embeddings: `text-embedding-3-small`.

Por decisão do produto, não usar modelos Terra, `gpt-5.4-mini` ou qualquer modelo Nano. O
`gpt-5-mini` permanece apenas como baseline histórico. O esforço `none` é explícito porque Luna
usa esforço médio por padrão; qualquer aumento deve ser validado novamente pelos gates.

## Resultado validado

A execução completa de 30 casos com o prompt `extraction-2026-08-15-v5` passou todos os gates:

| Métrica | Resultado | Gate |
| --- | ---: | ---: |
| Validade estrutural | 100% | ≥ 98% |
| Precisão de extração | 100% | ≥ 90% |
| Cobertura de extração | 90,24% | ≥ 80% |
| Respostas fundamentadas | 96,67% | ≥ 95% |
| Recuperação útil | 86,67% | ≥ 85% |
| Duplicação | 0% | ≤ 2% |

Foram consumidos 37.798 tokens de entrada e 7.284 de saída nas 30 extrações, mais 10.070 de
entrada e 2.732 de saída em 27 respostas. O custo de modelo estimado foi **US$ 0,021592** no total,
ou aproximadamente **US$ 0,000720 por caso**. O valor não inclui embeddings. O resultado
reproduzível está em `evaluation/live-report.json`.

## Preços de referência

Valores por 1 milhão de tokens em processamento padrão, consultados em 15 de agosto de 2026:

| Modelo | Entrada | Saída | Uso candidato |
| --- | ---: | ---: | --- |
| `gpt-5.6-luna` | US$ 0,20 | US$ 1,20 | extração e resposta do MVP |
| `text-embedding-3-small` | US$ 0,02 | — | busca vetorial |

Fontes oficiais: [GPT-5.6 Luna](https://developers.openai.com/api/docs/models/gpt-5.6-luna) e
[text-embedding-3-small](https://developers.openai.com/api/docs/models/text-embedding-3-small).

## Processo de comparação

1. Rodar uma amostra pequena para detectar incompatibilidade de schema ou prompt.
2. Executar os 30 casos somente nas combinações que passam a amostra.
3. Comparar gates, tokens consumidos, latência e custo por caso aprovado.
4. Fixar um snapshot de modelo quando a estabilidade for mais importante que atualizações automáticas.
5. Reexecutar o benchmark ao mudar modelo, prompt, schema ou regras de consolidação.

O avaliador aceita modelos sem editar o arquivo de ambiente:

```bash
python scripts/evaluate_live.py \
  --extraction-model gpt-5.6-luna \
  --answering-model gpt-5.6-luna \
  --case-ids syn-001,syn-002,syn-004,syn-009,syn-012,syn-013,syn-017,syn-018,syn-023,syn-024,syn-030
```

Resultados limitados são marcados como benchmark e nunca liberam o piloto. A execução completa
remove `--case-limit` ou `--case-ids`.

Para cargas offline que toleram retorno em até 24 horas, a Batch API reduz o preço de entrada e
saída em 50%. Ela é apropriada para avaliação e backfill, não para perguntas interativas.
[Referência oficial da Batch API](https://platform.openai.com/docs/api-reference/batch/object?api-mode=responses).
