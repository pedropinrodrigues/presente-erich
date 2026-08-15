# Etapa 04 — Extração e memória

## Objetivo

Converter fontes persistidas em memória estruturada, sem permitir que o modelo de IA grave livremente no banco. O sistema deve guardar evidência e histórico para explicar ou corrigir cada fato.

## Pré-requisitos e acessos

- Etapa 03 aprovada.
- Chave da OpenAI disponível somente no model gateway.
- JSON Schemas de extração versionados e fixtures de transcrição revisadas.

## Implementação, na ordem

1. Criar model gateway com SDK Python da OpenAI, Responses API, `store=false`, timeout, limite de tentativas, rastreio de versão e validação de JSON Schema.
2. Implementar extrator para pessoas, organizações, projetos, fatos, decisões e compromissos.
3. Exigir que cada candidato tenha trecho de evidência, tipo e confiança.
4. Validar candidatos no serviço de domínio antes da persistência.
5. Criar ou vincular entidades somente quando a resolução for suficientemente confiável.
6. Persistir fatos como `proposed` ou `current`; quando houver atualização comprovada, marcar a versão anterior como `superseded`.
7. Criar eventos de auditoria e índices de busca para registros consolidados.

## Entregáveis

- Job de extração ponta a ponta.
- JSON Schema e contrato interno de candidatos.
- Serviços de consolidação temporal e evidências.
- Fixtures com resultados esperados e versões de prompt/modelo registradas.

## Testes obrigatórios

- JSON inválido é rejeitado e recebe somente uma nova tentativa de correção.
- Candidato sem trecho de evidência não vira fato atual.
- Confiança abaixo de `0.70` mantém o candidato como `proposed`.
- Novo prazo substitui corretamente o prazo atual sem apagar o histórico.
- Execução repetida não duplica entidades, fatos, compromissos ou auditoria.
- Texto da transcrição não é tratado como instrução para o sistema ou ferramentas.

## Gate de pronto

- Uma fonte de referência gera memória navegável e ligada à transcrição de origem.
- Um fato atual pode ser explicado por fonte, trecho, data e versão.
- O modelo está isolado do banco e não possui ferramentas de ação.
- O workflow continua explícito em Python; LangGraph/LangChain só entram mediante necessidade comprovada.

## Não fazer nesta etapa

- Implementar grafo especializado, agentes autônomos ou ferramentas externas.
- Promover inferências incertas à verdade atual automaticamente.
