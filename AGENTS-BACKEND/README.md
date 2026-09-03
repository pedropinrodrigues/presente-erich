# Agents & Backend MVP

Backend em FastAPI que transforma transcrições em memória pesquisável. O Luna produz uma decisão
estruturada para responder, esclarecer, pedir confirmação ou delegar; um orquestrador Terra pondera
e executa ações persistidas por tools controladas. Supabase hospeda Auth, PostgreSQL e o gateway
público do webhook. API e worker Python podem executar localmente ou como serviços separados no
Northflank; o repositório inclui imagem Docker, provisionamento idempotente, health checks,
heartbeat do worker e canários de rollout.

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

Somente chats privados estão habilitados. O canal aceita texto, mensagens de voz e arquivos de
áudio dentro dos limites configurados; outras mídias, grupos e canais continuam fora do escopo. Um
chat não acessa nenhum workspace antes da prova de posse pelo deep link. O adaptador Meta WhatsApp
permanece no repositório, mas não é o provedor ativo quando `MESSAGING_PROVIDER=telegram`.

## Conectar Gmail, Google Calendar e WhatsApp Business

Com `COMPOSIO_ENABLED=true` e as credenciais Composio preenchidas, o usuário pode pedir no próprio
Telegram: “conecte meu Gmail”, “conecte meu Google Calendar” ou “conecte meu WhatsApp Business”. O
orquestrador devolve um link privado; a conta fica vinculada somente ao usuário e workspace que
originaram o pedido.

Gmail e Google Calendar aceitam múltiplas contas por usuário. Exemplos no Telegram: “adicione outra
conta Gmail e chame de Trabalho”, “liste minhas contas conectadas”, “use a conta Pessoal como
padrão” e “resuma os emails de todas as contas”. Leituras sem conta específica agregam todas as
conexões ativas; rascunhos, envios e alterações usam a conta padrão, salvo quando o usuário indicar
outra conta explicitamente. Cada conexão tem `account_id`, apelido e marcador de conta padrão.

Leituras e consultas são R0, rascunhos de email são R1 e qualquer envio ou alteração externa é R2.
Uma ação R2 fica pendente e só é executada depois de uma confirmação explícita em nova mensagem.
O modelo nunca recebe API keys ou tokens OAuth: o backend chama uma sessão MCP limitada às tools da
intenção, e o Composio guarda as credenciais da conta.

## Conectar Bitrix24 por MCP

Com `BITRIX24_MCP_ENABLED=true`, o usuário pode pedir “conecte meu Bitrix24”. O backend devolve um
link temporário: o estado secreto fica no fragmento da URL, a página envia o token somente no corpo
HTTPS e o valida em `https://mcp.bitrix24.com/mcp/`. Depois da validação, o token é armazenado com
Fernet e a conexão só fica ativa quando o usuário envia `confirmo` em uma nova mensagem no canal.

CRM e tarefas usam um catálogo local. Leituras são R0; criação e alteração são R2 e exigem nova
confirmação. Os slugs `BITRIX24_TOOL_*` devem ser preenchidos com os nomes revisados retornados por
`list_tools` no tenant piloto; slugs vazios não são oferecidos ao agente. Consulte
`docs/16-bitrix24-mcp-integration-plan.md` para configuração, segurança e rollout.

## Enviar transcrições do MacWhisper

Com `MACWHISPER_WEBHOOK_ENABLED=true`, um usuário já vinculado ao Telegram envia `/macwhisper` e
recebe uma única vez sua URL de Custom Webhook. O backend guarda somente o hash do segredo,
transforma `{title, transcript}` em uma ingestão idempotente e redige a URL persistida depois da
entrega no Telegram. `/revogarmacwhisper` invalida a URL imediatamente. Consulte
`docs/17-macwhisper-new-user-guide.md` para o onboarding.

Para testar e publicar o callback OAuth:

```bash
make test-composio-edge
make deploy-composio-edge
```

O callback configurado no Composio deve ser:

```text
https://onuxlluzwlnkhbsfiind.supabase.co/functions/v1/composio-callback
```

## Pesquisa na internet

Pedidos que dependem de notícias, fatos atuais ou fontes externas são delegados pelo Luna ao
orquestrador Terra. A tool `research_web` usa o `web_search` hospedado da Responses API e devolve
uma síntese somente quando há citações HTTPS válidas. O resultado final inclui uma lista curta de
fontes com URLs; a consulta é redigida na auditoria da tool.

A funcionalidade vem ativa por padrão. Os limites podem ser ajustados com
`WEB_RESEARCH_SEARCH_CONTEXT_SIZE`, `WEB_RESEARCH_MAX_TOOL_CALLS`,
`WEB_RESEARCH_MAX_SOURCES` e `WEB_RESEARCH_MAX_OUTPUT_TOKENS`, ou desativados com
`WEB_RESEARCH_ENABLED=false`. Nesta primeira versão, pesquisa web não é concedida a rotinas
programadas.

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
- `docs/11-composio-mcp-gateway-integration-plan.md`: plano para integrar produtos externos via
  Composio MCP sem contornar as políticas, confirmações e auditoria do orquestrador.
- `docs/12-scheduled-automations.md`: arquitetura e implementação de tarefas pontuais e rotinas
  recorrentes, autorização permanente limitada e briefings enriquecidos pela memória.
- `docs/13-backend-hosting-transition-spec.md`: especificação para retirar API e worker do Mac e
  publicá-los no Northflank com rollout, rollback, monitoramento e guardrails de custo zero.
- `docs/14-telegram-voice-transcription.md`: transcrição durável de voz e arquivos de áudio do
  Telegram com AssemblyAI.
- `docs/15-web-research.md`: pesquisa atual na internet pelo Responses API, fontes, limites e
  segurança.
- `docs/16-bitrix24-mcp-integration-plan.md`: arquitetura, segurança, fases e critérios para
  integrar CRM e tarefas pelo MCP oficial do Bitrix24.
- `docs/17-macwhisper-new-user-guide.md`: onboarding de uma nova conta pelo Telegram e configuração
  segura do Custom Webhook do MacWhisper.
