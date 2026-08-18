# Agents & Backend MVP

Backend em FastAPI que transforma transcrições em memória pesquisável. O Luna produz uma decisão
estruturada para responder, esclarecer, pedir confirmação ou delegar; um orquestrador Terra pondera
e executa ações persistidas por tools controladas. Supabase hospeda Auth, PostgreSQL e o gateway
público do webhook; API e worker Python executam localmente nesta fase.

## Preparação

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env.local
```

Preencha `.env.local` sem versionar segredos. Em seguida:

```bash
make migrate
make api
```

Quando necessário, `DATABASE_POOLER_URL` permite usar o pooler oficial do Supabase sem duplicar a
senha já armazenada em `DATABASE_URL`.

Em outro terminal, com o ambiente ativado:

```bash
make worker
```

## Qualidade

```bash
make check
make evaluate
make evaluate-conversation
```

Para executar os gates reais contra Supabase e OpenAI:

```bash
python scripts/evaluate_live.py
```

É possível comparar modelos sem alterar `.env.local`:

```bash
python scripts/evaluate_live.py \
  --extraction-model gpt-5.6-luna \
  --answering-model gpt-5.6-luna \
  --case-ids syn-001,syn-002,syn-004,syn-009,syn-012,syn-013,syn-017,syn-018,syn-023,syn-024,syn-030
```

Uma execução com `--case-limit` ou `--case-ids` é somente benchmark e nunca aprova o piloto.
Remova a opção para executar os 30 casos. O relatório fica em `evaluation/live-report.json`.
Se uma execução válida já gerou o cache
`evaluation/live-extractions.json`, use `--reuse-extractions` para comparar somente o modelo de
resposta sem pagar novamente pelas extrações. O cache só é reutilizado quando modelo, prompt e
schema coincidem. O relatório também registra tokens, latência acumulada e custo estimado de
extração/resposta; embeddings são identificados separadamente como não incluídos nessa estimativa.

A API publica documentação em `/docs`, health em `/health` e readiness em `/ready`.

## Testar o agente por HTTP

Com um JWT válido do Supabase, envie um identificador único por mensagem:

```bash
curl -X POST http://127.0.0.1:8000/v1/agent/turns \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"message_id":"teste-001","message":"Quais são minhas pendências?"}'
```

A resposta contém `conversation_id`, texto, `tools_used`, eventual `pending_action` e
`orchestration_task_id`. Reenvie
o mesmo `message_id` e conteúdo para obter a resposta persistida sem repetir efeitos. Para continuar
a conversa, envie o `conversation_id` retornado com um novo `message_id`.

## Ativar o Telegram

O Telegram é o canal ativo do MVP e requer `TELEGRAM_BOT_TOKEN`, `TELEGRAM_BOT_USERNAME` e
`TELEGRAM_WEBHOOK_SECRET`. O webhook público fica em
`supabase/functions/telegram-webhook`; ele valida o segredo configurado no Bot API, vincula o chat
por deep link e grava a mensagem idempotentemente. O worker Python continua responsável pelo
agente e pela outbox.

Teste e publique a função:

```bash
make test-telegram-edge
npx --yes supabase@latest login
npx --yes supabase@latest secrets set \
  --project-ref onuxlluzwlnkhbsfiind \
  TELEGRAM_BOT_TOKEN="$TELEGRAM_BOT_TOKEN" \
  TELEGRAM_WEBHOOK_SECRET="$TELEGRAM_WEBHOOK_SECRET"
make deploy-telegram-edge
```

```text
https://onuxlluzwlnkhbsfiind.supabase.co/functions/v1/telegram-webhook
```

Depois de criar o bot no BotFather:

1. registre essa URL com `setWebhook`, incluindo `TELEGRAM_WEBHOOK_SECRET` como `secret_token`;
2. vincule o usuário em `POST /v1/channels/telegram/accounts` usando o JWT;
3. abra o `verification_deep_link` retornado e toque em **Start**;
4. mantenha o worker ativo para processar mensagens e entregar a outbox; mantenha a API ativa para
   vínculo e rotas HTTP privadas durante o desenvolvimento.

Somente chats privados em texto estão habilitados. Um chat não acessa nenhum workspace antes da
prova de posse pelo deep link. O adaptador Meta WhatsApp permanece no repositório, mas não é o
provedor ativo quando `MESSAGING_PROVIDER=telegram`.

## Documentação

- `AGENTS-BACKEND.md`: escopo e critérios do MVP já implementado.
- `docs/01-mvp-architecture.md`: arquitetura atual.
- `docs/03-api-and-use-cases.md`: contratos HTTP e casos de uso.
- `docs/05-quality-and-evaluation.md`: métricas e gates.
- `docs/08-whatsapp-agent-tools-plan.md`: implementação do agente, tools, confirmações, canais de
  mensagem e pendências restantes para o piloto.
- `docs/09-orchestrated-agent-architecture.md`: separação entre agente rápido de consulta e
  orquestrador assíncrono de tarefas.
- `docs/10-telegram-invites-and-accounts.md`: fluxo de convites, criação de contas independentes e
  isolamento de workspaces pelo Telegram.
