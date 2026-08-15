# Etapa 06 — Correções, exclusão e qualidade

## Objetivo

Dar ao usuário controle real sobre a memória e provar que o backend é confiável antes de um piloto. Esta etapa fecha o MVP; sem ela, a memória não deve ser considerada pronta para uso real.

## Pré-requisitos e acessos

- Etapa 05 aprovada.
- Ambiente local conectado ao projeto Supabase do MVP.
- Dataset versionado de pelo menos 30 transcrições revisadas, sem dados reais sem consentimento.
- Responsável definido para revisar resultados qualitativos antes da liberação.

## Implementação, na ordem

1. Implementar `CorrectMemory` para substituir, contestar ou excluir informação com auditoria.
2. Implementar `DeleteMemory` e propagação para fontes, índices, embeddings e jobs pendentes.
3. Garantir que registros excluídos permanecem inacessíveis após retry ou reprocessamento.
4. Criar dataset e gabaritos de entidades, fatos, evidências e perguntas.
5. Automatizar avaliação de schema, extração, recuperação e sustentação de respostas.
6. Executar regressão completa localmente e registrar relatório versionado.

## Entregáveis

- Rotas de correção e exclusão com autorização de owner.
- Auditoria sem preservação do texto excluído.
- Dataset de avaliação, runner e relatório reproduzível.
- Checklist de liberação do piloto.

## Testes obrigatórios

- Correção muda a projeção atual, preserva a fonte e cria evento de auditoria.
- Exclusão remove dados de busca, respostas, embeddings e jobs futuros.
- Reprocessamento não recria memória derivada de fonte excluída.
- Todos os casos obrigatórios de `../docs/05-quality-and-evaluation.md` passam.
- Gates de qualidade são atingidos: schema ≥ 98%, precisão ≥ 90%, cobertura ≥ 80%, respostas fundamentadas ≥ 95%, recuperação útil ≥ 85% e duplicação ≤ 2%.

## Gate de pronto

- O fluxo completo funciona localmente: ingestão, memória, busca, resposta, correção e exclusão.
- Todos os gates quantitativos passam e a revisão humana não identifica comportamento de alta confiança sem evidência.
- O piloto fica limitado a memória e consulta; não habilita automações nem integrações de escrita.

## Não fazer nesta etapa

- Reduzir os gates para liberar mais rápido sem decisão explícita de produto.
- Habilitar ações externas apenas porque a memória já está disponível.
