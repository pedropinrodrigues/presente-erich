# Etapa 05 — Busca e respostas fundamentadas

## Objetivo

Permitir que o usuário recupere memória com segurança e veja por que o backend chegou à resposta. A primeira versão deve priorizar precisão e evidência, não conversação sofisticada.

## Pré-requisitos e acessos

- Etapa 04 aprovada.
- Dados de referência já processados com fontes e evidências.
- Extensão `pgvector` habilitada se a busca vetorial for adotada nesta entrega.

## Implementação, na ordem

1. Criar filtros obrigatórios por workspace, entidade, tipo, status e período.
2. Implementar busca textual e índices necessários; adicionar vetor como complemento quando houver embeddings disponíveis.
3. Criar `SearchMemory` com paginação por cursor.
4. Criar `GetEntity` com visão atual, histórico e relações permitidas pelo MVP.
5. Criar `AskMemory`: recuperar contexto, limitar a resposta às evidências e declarar incerteza quando necessário.
6. Retornar `answer`, `evidence`, `uncertainties` e `source_ids` em contrato estável.

## Entregáveis

- Rotas de busca, entidade e pergunta documentadas e autenticadas.
- Estratégia inicial de ranking e montagem de contexto.
- Resposta com links/identificadores de evidência para a UI apresentar.

## Testes obrigatórios

- Consulta por pendências retorna somente compromissos `open` do workspace correto.
- Pergunta sem evidência retorna incerteza, não uma afirmação inventada.
- Busca e resposta não retornam fontes excluídas.
- Pergunta temporal usa a versão atual e mostra a evidência correspondente.
- Paginação por cursor não repete ou omite itens.
- Usuário sem acesso à entidade recebe `404` ou `403` sem vazamento de existência indevido.

## Gate de pronto

- Localmente, com Supabase hospedado, é possível ingerir uma fonte, perguntar sobre ela e abrir a evidência citada.
- Afirmações relevantes são sustentadas por evidência ou apresentadas explicitamente como incerteza.
- Busca textual funciona mesmo que a camada vetorial esteja indisponível.

## Não fazer nesta etapa

- Criar chat com memória de sessão complexa.
- Implementar recomendação proativa, briefings ou ações externas.
