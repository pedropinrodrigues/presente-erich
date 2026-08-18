# Desenvolvimento local e banco hospedado

## Escopo atual

O FastAPI e o worker são processos iniciados na máquina de desenvolvimento. Banco, autenticação e
gateway público do Telegram ficam no mesmo projeto Supabase hospedado. O gateway é uma Edge
Function TypeScript/Deno; o Supabase não executa diretamente o FastAPI ou o worker Python.

```text
Máquina local
  ├─ FastAPI
  └─ Worker Python
          │
          ▼
Projeto Supabase
  ├─ PostgreSQL + Auth
  └─ Edge Function telegram-webhook ← Telegram Bot API
          │
          └─ grava entrada para o Worker Python
```

Deploy permanente da API e do worker será tratado depois que o fluxo do MVP estiver útil. A Edge
Function fornece desde já a URL HTTPS estável exigida pelo Telegram.

## Configuração local

Usar Python 3.12+, `venv` + `pip` para dependências e ambientes virtuais, `pytest` para testes, `ruff` para lint/formatação, Alembic para migrations e SQLAlchemy para acesso ao banco. As variáveis mínimas são:

```text
SUPABASE_URL
SUPABASE_ANON_KEY
SUPABASE_SERVICE_ROLE_KEY
DATABASE_URL
DATABASE_POOLER_URL
OPENAI_API_KEY
OPENAI_MODEL_EXTRACTION
OPENAI_MODEL_ANSWERING
OPENAI_MODEL_CONVERSATION
OPENAI_MODEL_ORCHESTRATION
```

`DATABASE_POOLER_URL` é opcional, mas recomendado quando a rede local não resolve o endpoint direto
IPv6 do Postgres. Ele pode omitir a senha; o backend reutiliza em memória a senha de `DATABASE_URL`.

Os limites dos runtimes possuem defaults seguros em `CONVERSATION_*` e `ORCHESTRATION_*`. Para
integrar o Telegram são
necessários:

```text
MESSAGING_PROVIDER=telegram
TELEGRAM_BOT_TOKEN
TELEGRAM_BOT_USERNAME
TELEGRAM_WEBHOOK_SECRET
```

As variáveis `WHATSAPP_*` permanecem opcionais enquanto o adaptador legado estiver inativo.

Essas variáveis ficam em `.env.local`, ignorado pelo Git. O repositório contém somente `.env.example` com nomes de variáveis, nunca valores.

## Banco e migrations

O Supabase é a única dependência hospedada do MVP. Migrations Alembic são aplicadas manualmente, em ordem, a partir da máquina local. Antes de cada migration nova, verificar em uma base local/descartável ou usar migration reversível; a aplicação não faz migrations automaticamente ao iniciar.

O banco precisa ter `pgvector` habilitado antes da busca vetorial. A busca textual e os filtros estruturados continuam funcionando mesmo que embeddings ainda não estejam configurados.

As migrations `20260816_0002` e `20260818_0003` criam a camada conversacional, a outbox ordenada,
`orchestration_tasks`, eventos e a separação de execuções entre agente rápido e orquestrador.
Aplique com:

```bash
make migrate
```

## Segurança e logs

- API valida JWT do Supabase em toda rota privada.
- Worker usa credencial de serviço apenas no ambiente local e nunca a expõe a clientes.
- Segredos, transcrições completas e respostas completas do modelo não entram nos logs.
- Chamadas à OpenAI usam `store=false` e registram apenas identificador, versão de prompt/modelo, duração, tokens e erro normalizado.
- O transcript de `remember_transcript` é substituído por hash e tamanho no log de execução da tool.
- O modelo não recebe credenciais, `user_id`, `workspace_id` ou uma tool HTTP/SQL genérica.
- O webhook do Telegram exige um segredo forte; o vínculo exige deep link temporário de uso único.

## Testes locais

O teste de integração usa um banco de teste ou um schema descartável no projeto Supabase, nunca
dados reais do usuário. Os testes cobrem migrations, isolamento por workspace, ingestão, worker,
extração, busca, schemas de tools, confirmações destrutivas, loop de function calling, idempotência
de turnos e webhook. Não é necessário staging para considerar a etapa pronta.

Para avaliar seleção de tools com o modelo real sem executar qualquer efeito de domínio:

```bash
make evaluate-conversation
```

O script usa resultados simulados de tools, grava `evaluation/conversation-report.json` e só aplica
os gates como código de saída quando executado com `--enforce-gates`.

Os gateways Deno possuem testes independentes:

```bash
make test-telegram-edge
make test-edge
```

O gateway Telegram é publicado sem validação JWT da plataforma porque o Bot API não envia JWT do
Supabase. A função permanece protegida por `X-Telegram-Bot-Api-Secret-Token` e só usa a chave de
serviço dentro do ambiente hospedado.
