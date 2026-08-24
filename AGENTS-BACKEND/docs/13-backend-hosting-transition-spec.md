# Spec de transição do backend para hospedagem permanente

## Estado

Especificado em 24/08/2026. Ainda não implementado.

Destino principal: **Northflank Sandbox**. Supabase continua como banco, Auth e gateway público das
integrações. Koyeb deixa de ser dependência do plano. Oracle Cloud Always Free permanece como
fallback caso o Northflank deixe de atender aos limites gratuitos.

Esta spec é a fonte de verdade para retirar API e worker do Mac sem alterar a arquitetura funcional
dos agentes.

## Objetivo

Publicar o FastAPI e o worker Python em infraestrutura permanente com custo financeiro zero para o
volume do MVP, preservando:

- processamento contínuo das mensagens do Telegram;
- execução pontual e recorrente dos agendamentos;
- outbox idempotente;
- isolamento por usuário e workspace;
- integrações OpenAI e Composio;
- migrations Alembic controladas;
- possibilidade de rollback para execução local.

Ao fim da transição, fechar ou suspender o Mac não poderá interromper o produto.

## Fora do escopo

- migrar PostgreSQL ou Auth para fora do Supabase;
- trocar o gateway Telegram já publicado como Edge Function;
- Kubernetes próprio, Redis, Celery ou uma fila externa;
- alta disponibilidade com múltiplas regiões;
- armazenar arquivos persistentes no Northflank;
- alterar modelos, prompts, tools ou regras de autorização dos agentes;
- garantir SLA de produção sobre um plano gratuito.

## Decisão de provedor

O Northflank Sandbox anuncia dois serviços gratuitos e always-on, sem suspensão por inatividade.
Isso corresponde aos dois processos atuais: API e worker. Também oferece build a partir do Git,
segredos, logs, endpoint HTTPS e health checks.

Referências:

- [Pricing e limites do Sandbox](https://northflank.com/pricing)
- [Introdução e deploy a partir de repositório](https://northflank.com/docs/v1/application/getting-started/introduction-to-northflank)

Render gratuito não atende porque o web service dorme e background workers gratuitos não estão
disponíveis. Cloud Run exigiria transformar o worker contínuo em um runtime acionado por eventos.
Oracle Cloud atende ao runtime contínuo, mas transfere manutenção de VM, patching e reinício para o
projeto e pode recuperar instâncias gratuitas consideradas ociosas.

## Arquitetura atual

```text
Telegram
   │ webhook
   ▼
Supabase Edge Function ──► PostgreSQL
                              │
                              ▼
Mac: worker Python ──► Luna / Terra / OpenAI / Composio
       │
       └─ outbox ──► Telegram

Mac: FastAPI ──► rotas privadas, vínculo de contas e operações HTTP
```

O webhook não chama o FastAPI para cada mensagem. A Edge Function grava a entrada no PostgreSQL e
o worker reivindica a mensagem com lease. Portanto, a troca do worker não requer mudar a URL
registrada no BotFather.

## Arquitetura alvo

```text
                         ┌─────────────────────────────────┐
Telegram ──► Supabase ──►│ PostgreSQL / Auth / Edge Funcs │
                         └──────────────┬──────────────────┘
                                        │
                         ┌──────────────▼──────────────────┐
                         │ Northflank                     │
Cliente HTTP ── HTTPS ──►│ agents-api-prod                │
                         │ agents-worker-prod ──► outbox  │──► Telegram
                         └───────┬─────────────┬───────────┘
                                 │             │
                                 ▼             ▼
                              OpenAI        Composio
```

Os dois serviços usam a mesma revisão de código e o mesmo banco. Nenhum estado indispensável fica
no filesystem do container.

## Contrato dos serviços

### `agents-api-prod`

- tipo: combined/deployment service;
- fonte: bundle público da branch `main` do repositório `presente-erich`, sem integração OAuth;
- diretório do projeto: `AGENTS-BACKEND`;
- build: Dockerfile versionado;
- comando: `uvicorn agents_backend.api.main:app --host 0.0.0.0 --port 8080`;
- porta pública: HTTP `8080`, publicada como HTTPS pelo Northflank;
- health check: `GET /health` para vida e `GET /ready` para prontidão com o banco;
- uma instância;
- build acionado pela API após os gates da branch principal.

`/health` comprova que o processo responde. `/ready` também comprova uma consulta simples ao
Supabase. Rotas `/v1` permanecem protegidas por JWT. Webhooks continuam protegidos por seus
segredos, mesmo que a rota FastAPI não seja o gateway principal do Telegram.

### `agents-worker-prod`

- tipo: combined/deployment service sem porta pública;
- mesmo bundle público, Dockerfile e revisão da API;
- comando: `python -m agents_backend.worker.main`;
- uma instância durante o piloto;
- execução always-on;
- reinício automático quando o processo terminar;
- acesso de saída a Supabase, Telegram, OpenAI e Composio.

O worker continua usando PostgreSQL, leases e `FOR UPDATE SKIP LOCKED`. A arquitetura já suporta
uma sobreposição temporária entre o worker local e o hospedado sem duplicar a ocorrência lógica,
mas a sobreposição deve existir apenas durante o canário.

### `agents-migrate-prod`

- tipo: job manual criado a partir da mesma imagem;
- comando: `alembic upgrade head`;
- nenhuma recorrência;
- executado uma única vez antes de uma versão que dependa de migration nova;
- falha impede o rollout da API e do worker.

Migrations não devem rodar automaticamente no start dos dois serviços, evitando corrida entre
instâncias e misturando falha de schema com falha de inicialização.

## Artefatos a implementar no repositório

1. `Dockerfile` multi-stage ou enxuto baseado em Python 3.13 slim:
   - instalar somente dependências de runtime;
   - instalar o pacote `agents-backend`;
   - executar como usuário sem privilégios;
   - não copiar `.env.local`, `.git`, caches, testes ou relatórios de avaliação;
   - não definir segredo por `ARG` ou `ENV` na imagem.
2. `.dockerignore` com segredos, ambiente virtual, caches e artefatos locais.
3. comandos de produção no `Makefile` para API, worker e migration, sem `--reload`.
4. smoke de deploy que valide `/health`, `/ready`, autenticação, banco e uma operação idempotente.
5. documentação operacional para deploy, rollback e investigação de fila.
6. script idempotente de provisionamento pela API, sem IDs sensíveis ou segredos versionados.

Uma única imagem deve servir aos três comandos. Isso elimina divergência de dependências entre API,
worker e migrations.

## Variáveis e segredos

Os valores reais ficam em um Secret Group do Northflank. `.env.local` continua ignorado e nenhum
valor é copiado para a spec, Dockerfile, logs ou configuração versionada.

Obrigatórios nos dois serviços enquanto `Settings` for compartilhado:

```text
APP_ENV=production
LOG_LEVEL=INFO
APP_TIMEZONE=America/Sao_Paulo
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
OPENAI_MODEL_EMBEDDING
MESSAGING_PROVIDER=telegram
TELEGRAM_BOT_TOKEN
TELEGRAM_BOT_USERNAME
TELEGRAM_WEBHOOK_SECRET
COMPOSIO_ENABLED
COMPOSIO_API_KEY
COMPOSIO_CALLBACK_URL
COMPOSIO_USER_ID_SECRET
COMPOSIO_GMAIL_AUTH_CONFIG_ID
COMPOSIO_GOOGLECALENDAR_AUTH_CONFIG_ID
COMPOSIO_WHATSAPP_AUTH_CONFIG_ID
```

Também devem ser replicados os limites de steps, tool calls, timeouts, scheduler, contexto e retries
presentes em `.env.local`. O deploy não é o momento de alterar comportamento do agente.

`DATABASE_POOLER_URL` é preferido pelos containers hospedados. A senha continua vindo de
`DATABASE_URL` quando a URL do pooler não a contém. O pool deve manter `pool_pre_ping` e reciclagem
de conexões.

Após o primeiro deploy, separar segredos de API e worker poderá reduzir privilégios, mas isso exige
dividir a validação atual de `Settings` e não faz parte do corte inicial.

## Confiabilidade obrigatória antes do corte

O incidente de 24/08/2026 mostrou que um worker pode permanecer vivo como processo enquanto
sucessivas falhas `DBAPIError` deixam mensagens em `received`. Reinício manual recuperou a fila,
mas apenas o restart automático por crash não detectaria esse estado.

Antes de desligar o worker local, implementar:

1. timeout limitado para abrir conexão e executar operações do ciclo;
2. descarte do pool após erro de conexão, preservando backoff com teto;
3. heartbeat persistido por instância do worker;
4. registro estruturado com etapa do ciclo, tipo e mensagem sanitizada do erro;
5. monitor de lag para a mensagem `received`, outbox `pending`, tarefa `queued` e run agendada;
6. reconciliação de tarefas `waiting_confirmation` cuja ação já expirou, falhou ou foi cancelada;
7. encerramento não zero quando o worker ultrapassar o limite de ciclos sem conseguir consultar o
   banco, permitindo ao Northflank reiniciar o container.

Valores iniciais propostos:

```text
DATABASE_CONNECT_TIMEOUT_SECONDS=10
DATABASE_COMMAND_TIMEOUT_SECONDS=30
WORKER_CYCLE_TIMEOUT_SECONDS=600
WORKER_MAX_CONSECUTIVE_INFRA_FAILURES=5
WORKER_HEARTBEAT_INTERVAL_SECONDS=30
QUEUE_LAG_WARNING_SECONDS=60
```

Falhas de OpenAI ou Composio pertencem ao job e usam retry/idempotência existentes. Falha de
infraestrutura que impede o próprio loop de consultar filas pertence ao processo e pode provocar
restart controlado.

## Observabilidade mínima

O deploy deve permitir responder sem acessar o Mac:

- qual commit está rodando em cada serviço;
- quando ocorreu o último ciclo saudável do worker;
- idade da mensagem de entrada mais antiga ainda não concluída;
- quantidade e idade de itens em `received`, `processing`, `pending`, `queued` e `retrying`;
- última falha por etapa do worker;
- estado e duração de cada run agendada;
- resposta de `/health` e `/ready`;
- consumo de CPU e memória dos dois serviços.

Alertas iniciais:

- API `/ready` falha por dois minutos;
- heartbeat do worker atrasado por mais de 90 segundos;
- mensagem `received` por mais de 60 segundos sem lease;
- outbox `pending` por mais de 60 segundos;
- três falhas consecutivas de infraestrutura;
- rotina em `needs_attention`.

O canal principal de alerta não deve depender exclusivamente do mesmo worker que está sendo
monitorado.

## Estratégia de implementação

### Etapa 1 — preparação do runtime

- adicionar Dockerfile e `.dockerignore`;
- executar imagem localmente para API, worker e migration;
- garantir shutdown por `SIGTERM` e nenhum processo órfão;
- executar suíte completa e smoke autenticado dentro da imagem.

**Gate:** a imagem inicia os dois comandos e `/ready` consulta o Supabase.

### Etapa 2 — resiliência do worker

- adicionar timeouts e limite de falhas consecutivas;
- persistir heartbeat e métricas de lag;
- reconciliar confirmações encerradas;
- testar desconexão do PostgreSQL, recuperação e restart;
- confirmar que lease vencido recupera trabalho sem duplicar efeito.

**Gate:** simular indisponibilidade do banco não deixa o processo vivo e improdutivo por tempo
indefinido.

### Etapa 3 — provisionamento Northflank

- criar projeto no Sandbox;
- apontar os builds para o bundle público da branch `main`, sem instalar o app do GitHub;
- criar Secret Group;
- criar job de migration;
- criar API com porta e health checks;
- criar worker sem porta pública;
- registrar a URL HTTPS gerada como URL base da API dos clientes futuros.

**Gate:** API e worker exibem o mesmo commit e passam prontidão.

### Etapa 4 — canário

1. aplicar migrations pelo job;
2. publicar a API e validar `/health`, `/ready` e JWT;
3. iniciar o worker hospedado mantendo o local por no máximo 15 minutos;
4. enviar uma consulta simples pelo Telegram;
5. executar uma consulta de memória;
6. criar um lembrete pontual para dois minutos;
7. confirmar uma leitura Gmail e Calendar sem escrita;
8. observar leases, outbox e ausência de duplicatas;
9. parar o worker local;
10. repetir Telegram e lembrete somente com o worker hospedado.

**Gate:** todas as respostas chegam uma única vez e o Mac pode permanecer desligado.

### Etapa 5 — estabilização

- manter logs e alertas sob observação por 48 horas;
- validar reinício manual do container durante uma tarefa recuperável;
- conferir custo do workspace e permanência no Sandbox;
- atualizar a documentação que ainda afirma que API e worker são locais;
- remover instruções do Koyeb e registrar a revisão implantada.

**Gate:** nenhuma fila ultrapassa o limite de lag e nenhum recurso pago foi criado.

## Rollback

Rollback do worker:

1. desabilitar ou escalar `agents-worker-prod` para zero;
2. iniciar o worker local usando a mesma revisão ou a revisão anterior compatível com o schema;
3. aguardar recuperação automática de leases expirados;
4. verificar mensagens, tarefas, scheduled runs e outbox;
5. nunca corrigir a fila apagando registros manualmente.

Rollback da API:

1. restaurar a revisão anterior no Northflank;
2. se necessário, usar temporariamente a API local;
3. manter o webhook Telegram no Supabase, pois ele não depende da URL da API;
4. só reverter migration quando o downgrade for explicitamente suportado e testado.

O banco é a fonte de verdade, portanto trocar processos não deve exigir copiar dados.

## Segurança

- usar somente Secret Groups, nunca variáveis embutidas na imagem;
- impedir publicação de `.env.local` e credenciais em logs;
- restringir acesso ao projeto Northflank e ao repositório GitHub;
- manter uma instância de cada serviço no piloto;
- executar container como usuário não root;
- expor somente a porta da API;
- manter JWT e segregação por workspace em todas as rotas privadas;
- validar assinatura/segredo em callbacks e webhooks;
- não conceder shell, MCP genérico ou executor de comandos aos agentes por causa do deploy;
- rotacionar qualquer segredo que tenha sido exposto fora dos gerenciadores autorizados.

## Guardrails de custo zero

- exatamente dois serviços gratuitos: API e worker;
- nenhum banco, volume ou serviço pago no Northflank;
- Supabase permanece no plano existente;
- uma instância por serviço, sem autoscaling pago;
- alertas de uso e revisão do painel após cada deploy;
- nenhuma promoção automática para plano pago;
- se os limites gratuitos forem insuficientes, interromper e decidir entre otimização, Cloud Run
  event-driven ou Oracle Always Free antes de gerar cobrança.

## Critérios de aceite

1. API e worker executam no Northflank a partir do mesmo commit.
2. `/health` e `/ready` permanecem saudáveis sem nenhum processo local.
3. Uma mensagem Telegram é persistida, processada e respondida uma única vez.
4. Um lembrete pontual é entregue no horário dentro da tolerância configurada.
5. Uma rotina recorrente continua sendo reivindicada após restart do worker.
6. Gmail e Calendar leem somente contas conectadas do usuário correto.
7. Falha temporária do PostgreSQL é limitada por timeout e recuperada ou provoca restart.
8. O monitor detecta worker vivo porém sem ciclo saudável.
9. Deploy concorrente ou sobreposição de workers não duplica outbox, tool R2 ou scheduled run.
10. Migrations são executadas por job explícito e deixam audit trail.
11. Nenhum segredo aparece na imagem, repositório, resposta HTTP ou log.
12. Desligar o Mac por 24 horas não interrompe Telegram nem agendamentos.
13. O painel do Northflank não contém nenhum recurso faturável.

## Entradas necessárias do proprietário

Antes da Etapa 3, o proprietário precisa:

1. criar ou liberar uma conta Northflank no tier Sandbox;
2. gerar um token temporário da API e armazená-lo somente em `.env.local`;
3. manter disponíveis os valores atuais de `.env.local` para inserção no Secret Group;
4. confirmar que a branch de produção será `main`;
5. não publicar tokens ou chaves em issue, commit, screenshot ou mensagem compartilhável.

Depois dessas entradas, preparação do repositório, configuração dos serviços, migration, smoke,
canário e verificação podem seguir sem decisões adicionais enquanto não houver cobrança ou mudança
de escopo.

## Definição de concluído

A transição termina somente depois que o worker local estiver parado, o Mac puder ser desligado e
uma mensagem comum mais um agendamento real forem processados integralmente pelo Northflank. Criar
os serviços ou obter `/health = ok` isoladamente não conclui o deploy.
