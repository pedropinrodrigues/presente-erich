# Pesquisa na internet pelo Responses API

## Estado

Implementado em 27 de agosto de 2026. A pesquisa web é uma capacidade R0 do orquestrador e usa a
tool hospedada `web_search` da OpenAI pela Responses API. Não há crawler, proxy HTTP genérico ou
credencial de mecanismo de busca adicional no backend.

## Fluxo

```text
pedido atual/externo
        │
        ▼
Luna: delegate + web_research
        │ tarefa persistida
        ▼
Terra recebe apenas research_web
        │ function call tipada
        ▼
ModelGateway.research_web
        │ Responses API + web_search
        ▼
síntese + citações válidas → resposta final + Fontes
```

O contrato de `research_web` recebe uma consulta de 3 a 1.000 caracteres e, opcionalmente, até dez
domínios permitidos. O filtro aceita somente hostnames, como `openai.com`; URLs, caminhos, portas e
credenciais são rejeitados antes da chamada externa.

O gateway envia `store=false`, `tool_choice=required`, `parallel_tool_calls=false`, localização
aproximada configurável e `include=["web_search_call.action.sources"]`. O número de chamadas
hospedadas, o contexto de busca, as fontes expostas e os tokens de saída têm limites próprios.

## Fundamentação e segurança

- conteúdo encontrado na web é tratado como dado não confiável e não como instrução;
- somente anotações `url_citation` com URL HTTP(S), hostname e sem credenciais viram fontes;
- fontes são deduplicadas e limitadas antes de chegar ao orquestrador;
- uma resposta sem ao menos uma fonte citada falha com `web_research_ungrounded`;
- o Terra preserva incertezas e entrega fontes em texto simples compatível com Telegram;
- a consulta é armazenada na execução apenas como tamanho e SHA-256, não em texto aberto;
- `ModelRun` registra modelo, prompt, request ID, latência e tokens para observabilidade;
- a execução da tool é idempotente e o replay não refaz nem cobra uma nova pesquisa.

O `safety_identifier` é derivado por hash do usuário autenticado. IDs internos, tokens e
credenciais não são incluídos na consulta. A tool não executa ações externas nem escreve memória.

## Política de acesso

`web_research` recebe somente `research_web`. A intenção `compound` também pode pesquisar quando o
pedido combina web com outro domínio. A capacidade não faz parte de `automation`, portanto rotinas
programadas não pesquisam a internet nesta primeira versão; isso evita autorizações permanentes e
custo recorrente implícito.

Com `WEB_RESEARCH_ENABLED=false`, a definição da tool não é enviada ao orquestrador.

## Configuração

| Variável | Padrão | Função |
| --- | --- | --- |
| `WEB_RESEARCH_ENABLED` | `true` | Ativa ou remove a tool do catálogo. |
| `WEB_RESEARCH_SEARCH_CONTEXT_SIZE` | `medium` | Contexto hospedado `low`, `medium` ou `high`. |
| `WEB_RESEARCH_MAX_TOOL_CALLS` | `3` | Máximo de chamadas hospedadas por pesquisa. |
| `WEB_RESEARCH_MAX_SOURCES` | `5` | Máximo de fontes citadas devolvidas ao Terra. |
| `WEB_RESEARCH_MAX_OUTPUT_TOKENS` | `1600` | Limite da síntese produzida pela pesquisa. |
| `WEB_RESEARCH_COUNTRY` | `BR` | País aproximado em ISO alpha-2. |

A pesquisa usa `OPENAI_MODEL_ORCHESTRATION` e
`OPENAI_REASONING_EFFORT_ORCHESTRATION`. O modelo configurado precisa oferecer suporte ao
`web_search` da Responses API.

## Validação

Os testes cobrem o contrato enviado à OpenAI, filtro opcional de domínios, descarte de esquemas de
URL inseguros, exigência de fonte, auditoria, redação da consulta e replay idempotente. Testes
automatizados usam um cliente falso e não acessam nem cobram a API real.

Referência oficial: [Web search na Responses API](https://developers.openai.com/api/docs/guides/tools-web-search).
