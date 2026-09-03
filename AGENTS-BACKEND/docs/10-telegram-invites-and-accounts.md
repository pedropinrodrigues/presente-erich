# Guia de desenvolvimento — convites e contas pelo Telegram

## Estado

Implementado no backend e no webhook em 24/08/2026. A migração `20260824_0009` está aplicada no
Supabase. O repositório também possui diagnóstico e canário transacional do aceite. Publicação da
Edge Function, revisão ativa de API/worker e isolamento com uma segunda conta real continuam sendo
verificações operacionais, não inferências feitas apenas a partir do código.

## Objetivo

Permitir que o administrador da plataforma convide outra pessoa por um link do Telegram. Ao aceitar
o convite, a pessoa recebe uma **conta completa da aplicação**, com identidade, workspace, memória,
conversas, pendências e histórico próprios.

O convite é somente o mecanismo de entrada. Ele não cria acesso ao workspace de quem convidou.

## Decisões de produto

- Cada pessoa possui uma conta interna da aplicação.
- Cada conta possui um workspace pessoal e o papel único `owner`.
- O convidante não pode consultar, alterar ou excluir os dados da conta criada pelo convidado.
- Inicialmente, somente um administrador humano explícito da plataforma pode gerar convites.
- Pedro é o administrador inicial, identificado por seu `app_users.id` configurado no ambiente.
- O backend aplica a autorização; Luna, Terra e qualquer outro modelo não podem conceder o papel.
- Não haverá cota diária ou mensal de mensagens.
- Não haverá cota de tokens, tools, memória, convites ou tempo de uso por conta.
- Não haverá plano especial ou permissões reduzidas para contas originadas por convite.
- Grupos do Telegram continuam fora do escopo; somente chats privados são aceitos.
- A identidade inicial da nova conta será o `from.id` fornecido pelo Telegram.
- Uma identidade Supabase poderá ser associada posteriormente sem trocar o ID interno do usuário.

Os controles de integridade continuam obrigatórios: token de convite de uso único, validade
temporal, consumo atômico, idempotência, tamanho máximo de payload e limites internos de passos do
agente. Esses controles evitam replay e falhas técnicas; não constituem quotas de uso da conta.

## Experiência do usuário

### Criação do convite

O administrador pode usar o atalho determinístico:

```text
/convidar
```

Ou pedir em linguagem natural:

```text
Convide uma pessoa para usar o bot.
```

Ambos chamam o mesmo caso de uso interno e passam pela mesma autorização. O comando não precisa
acionar um modelo e, portanto, deve ser a opção preferencial para o caminho rápido.

Uma conta comum que tentar criar um convite recebe uma negativa determinística. Ela continua sendo
uma conta completa para memória, conversas, integrações e automações; apenas a administração de
cadastros permanece centralizada.

O bot responde:

```text
Convite criado.

Quem abrir este link receberá uma conta pessoal e separada da sua.
O convite pode ser usado uma vez e expira em 24 horas.
```

Com os botões:

- `Compartilhar convite`
- `Revogar convite`
- `Ver meus convites`

O link usa deep linking nativo do Telegram:

```text
https://t.me/agente_erich_bot?start=invite_TOKEN_OPACO
```

O botão de compartilhamento pode abrir:

```text
https://t.me/share/url?url=DEEP_LINK&text=MENSAGEM
```

### Aceite

```text
Convidante gera link
        │
        ▼
Convidado abre o bot e toca em Start
        │
        ▼
Telegram envia /start invite_TOKEN
        │
        ▼
Edge Function valida e consome o convite em transação
        │
        ├─ cria conta interna
        ├─ cria identidade Telegram
        ├─ cria workspace pessoal
        └─ vincula o chat ao novo workspace
        │
        ▼
Bot envia boas-vindas e a conta já pode usar o agente
```

O comando contendo o token nunca é encaminhado ao modelo e não entra no histórico conversacional.

### Boas-vindas

Depois do aceite:

```text
Bem-vindo à Luna! 👋

Sua conta pessoal está pronta e este Telegram já está conectado.

Você pode escrever ou enviar áudio normalmente — não precisa decorar comandos. Experimente:
• Guarde que a reunião do Projeto Atlas ficou para sexta.
• Quais são minhas pendências?
• Pesquise na internet as novidades sobre um assunto.
• Amanhã às 9h, me lembre de ligar para a Marina.

Quer usar o MacWhisper? Envie /macwhisper, copie a URL pessoal e siga as instruções recebidas.

Comandos úteis:
• /ajuda — veja capacidades e comandos.
• /minhaconta — confira sua conta.
• /macwhisper — configure transcrições.
• /revogarmacwhisper — invalide a URL anterior.

Quando tiver dúvida, diga o que deseja fazer e eu explico o próximo passo.
```

O `first_name` do Telegram pode preencher inicialmente `display_name`. O usuário poderá alterá-lo
com `/minhaconta`. Username, nome ou foto do Telegram nunca são usados como autoridade de acesso.

### Usuário já cadastrado

Se a identidade Telegram já pertencer a uma conta ativa, não deve ser criada uma segunda conta. O
convite é marcado como aceito pela identidade existente e o bot responde:

```text
Seu Telegram já possui uma conta. Continue usando normalmente.
```

O usuário continua no workspace que já possuía; o convite não concede acesso ao workspace do
convidante.

### Convite inválido

Para token expirado, revogado, inexistente ou já utilizado por outra identidade:

```text
Este convite não está mais disponível. Peça um novo link à pessoa que convidou você.
```

Não informar se o token já existiu, quem o criou ou quem o consumiu.

## Modelo de identidade

Hoje o sistema usa diretamente o `sub` do Supabase Auth como `user_id`. Para uma conta criada pelo
Telegram existir independentemente de login web, a aplicação precisa separar **usuário interno** de
**identidade externa**.

### `app_users`

| Campo | Tipo | Regra |
| --- | --- | --- |
| `id` | UUID | Identificador interno e imutável. |
| `display_name` | texto anulável | Nome escolhido pelo usuário. |
| `locale` | texto anulável | Inicialmente `language_code` do Telegram. |
| `timezone` | texto anulável | Pode ser configurado depois. |
| `status` | texto | `active`, `disabled` ou `deleted`. |
| `created_at` | timestamptz | Criação da conta. |
| `updated_at` | timestamptz | Última atualização. |

Não usar `telegram_user_id`, e-mail ou telefone como chave primária da conta.

### `user_identities`

| Campo | Tipo | Regra |
| --- | --- | --- |
| `id` | UUID | Chave da identidade. |
| `user_id` | UUID | FK para `app_users.id`. |
| `provider` | texto | Inicialmente `telegram` ou `supabase`. |
| `provider_subject` | texto | `from.id` do Telegram ou `sub` do Supabase. |
| `identity_metadata` | JSON | Somente metadados não autoritativos necessários. |
| `verified_at` | timestamptz | Momento em que o provedor comprovou a identidade. |
| `created_at` | timestamptz | Criação do vínculo. |

Constraint obrigatória:

```text
UNIQUE(provider, provider_subject)
```

No Telegram, usar `message.from.id` como `provider_subject`. O `chat.id` permanece o destino do
canal em `channel_accounts.external_account_id`. Em chats privados os valores normalmente
coincidem, mas o domínio não deve depender dessa coincidência.

### `channel_invites`

| Campo | Tipo | Regra |
| --- | --- | --- |
| `id` | UUID | ID opaco apresentado em listagens. |
| `created_by_user_id` | UUID | Conta que criou o convite. |
| `created_by_workspace_id` | UUID | Workspace de origem para auditoria, nunca compartilhado. |
| `token_hash` | char(64) | SHA-256 do token; o token original não é persistido. |
| `purpose` | texto | `personal_account`. |
| `status` | texto | `pending`, `accepted`, `revoked` ou `expired`. |
| `expires_at` | timestamptz | Inicialmente 24 horas após a criação. |
| `accepted_by_user_id` | UUID anulável | Conta criada ou já existente. |
| `accepted_provider_subject` | texto anulável | Identidade Telegram que consumiu o convite. |
| `accepted_at` | timestamptz anulável | Momento do consumo. |
| `revoked_at` | timestamptz anulável | Momento da revogação. |
| `created_at` | timestamptz | Momento da criação. |

Constraints e índices:

```text
UNIQUE(token_hash)
INDEX(status, expires_at)
INDEX(created_by_user_id, created_at DESC)
```

Não adicionar `max_invites`, `remaining_uses`, `daily_messages`, `token_budget`, `plan` ou campos de
quota. Cada convite individual continua sendo de uso único porque é uma credencial de cadastro.

## Migração dos dados existentes

Criar uma migration posterior a `20260816_0002` com a seguinte ordem:

1. Criar `app_users`, `user_identities` e `channel_invites`.
2. Inserir um `app_users` para cada `owner_user_id` existente, preservando o mesmo UUID.
3. Criar identidade `supabase` usando o UUID existente como `provider_subject` quando aplicável.
4. Para cada `channel_account` Telegram ativo, criar também a identidade `telegram` usando o
   identificador externo disponível.
5. Adicionar FK de `workspaces.owner_user_id` para `app_users.id`.
6. Adicionar FKs gradualmente aos campos `user_id` das tabelas conversacionais.
7. Validar constraints somente depois do backfill.

Preservar os UUIDs atuais evita reescrever fontes, memórias, auditorias, conversas e ações pendentes.

## Resolução de identidade

`Identity.user_id` passa a significar sempre `app_users.id`.

### Requisição HTTP autenticada

O JWT continua validado pelo Supabase. Depois da validação:

1. Ler o `sub` do token.
2. Resolver `user_identities(provider='supabase', provider_subject=sub)`.
3. Criar a conta interna e a identidade na primeira autenticação, se ainda não existirem.
4. Resolver o workspace em que `owner_user_id` é o ID interno.
5. Construir `RequestContext` sem aceitar IDs fornecidos pelo corpo da requisição.

### Mensagem do Telegram

Depois do vínculo:

1. Resolver `channel_accounts(provider='telegram', external_account_id=chat_id, active=true)`.
2. Obter `user_id` e `workspace_id` persistidos no vínculo.
3. Construir `RequestContext` com esses valores.
4. Nunca permitir que o modelo selecione ou altere a identidade.

## Token de convite

Gerar pelo menos 256 bits aleatórios:

```python
token = secrets.token_urlsafe(32)
token_hash = hashlib.sha256(token.encode()).hexdigest()
```

O payload do deep link deve ficar abaixo do limite aceito pelo Telegram. Usar um prefixo curto:

```text
invite_<token>
```

Regras:

- persistir somente `token_hash`;
- nunca registrar token ou deep link completo em logs;
- devolver o token somente na resposta que cria o convite;
- comparar pelo hash;
- não reutilizar token revogado ou expirado;
- não transportar IDs de workspace no link;
- não incluir informação pessoal no payload.

## Consumo atômico no PostgreSQL

O aceite não pode ser uma sequência de inserts independentes na Edge Function. Criar uma função
PostgreSQL chamada, por exemplo, `accept_telegram_invite` e invocá-la por RPC com a service role.

Entrada mínima:

```text
p_token_hash
p_chat_id
p_telegram_user_id
p_profile_metadata
```

A função deve, em uma única transação:

1. Executar `SELECT ... FOR UPDATE` no convite.
2. Verificar `status` e `expires_at`.
3. Procurar a identidade Telegram por `provider_subject`.
4. Se já existir, reutilizar sua conta e workspace.
5. Caso contrário, criar `app_users`, `user_identities` e `workspaces`.
6. Criar ou ativar `channel_accounts` para o chat privado.
7. Marcar o convite como `accepted`.
8. Gravar `audit_events` sem armazenar o token.
9. Retornar somente IDs necessários e um código seguro de resultado.

Resultados estáveis:

```text
created
already_registered
already_accepted_by_same_identity
expired
revoked
unavailable
```

Se o Telegram repetir o mesmo update, `already_accepted_by_same_identity` deve ser tratado como
sucesso idempotente. Se outra identidade tentar reutilizar o token, retornar somente `unavailable`.

A função deve usar `SECURITY DEFINER`, `search_path` fixo e permissão de execução restrita à service
role. O cliente público não pode chamar essa RPC.

## Alterações na Edge Function

Estender `supabase/functions/telegram-webhook` para:

1. Reconhecer `/start invite_<token>` antes de qualquer ingestão conversacional.
2. Calcular o hash e chamar `accept_telegram_invite`.
3. Enviar a mensagem de boas-vindas em caso de sucesso.
4. Enviar erro genérico em caso de convite indisponível.
5. Não persistir o comando de convite em `channel_messages`.
6. Não chamar o agente durante cadastro.
7. Continuar aceitando somente chats privados e updates de texto.

Comportamento de `/start` sem payload:

- conta ativa: mostrar ajuda curta;
- identidade sem conta: informar que é necessário um convite;
- nunca criar conta pública automaticamente sem token.

## Casos de uso e comandos

### `CreateTelegramInvite`

Entrada:

```text
RequestContext autenticado
```

Saída:

```json
{
  "invite_id": "uuid",
  "deep_link": "https://t.me/agente_erich_bot?start=invite_...",
  "share_link": "https://t.me/share/url?...",
  "expires_at": "ISO-8601"
}
```

Exige que o usuário seja um administrador ativo da plataforma. Não exige confirmação em segundo
turno porque não altera dados do convidado e não compartilha o workspace do criador.

### `ListTelegramInvites`

Retorna convites criados pelo usuário com estado e datas. Não retorna `token_hash`, token, chat ID ou
dados da pessoa que aceitou.

### `RevokeTelegramInvite`

Só revoga convite `pending`. Um administrador pode revogar qualquer convite pendente; uma futura
política descentralizada poderá limitar contas comuns aos próprios convites. Após o aceite, revogar
o convite não afeta a conta criada. A suspensão de uma conta exige `/desativarconta` e confirmação.

### Atalhos do bot

| Comando | Comportamento |
| --- | --- |
| `/convidar` | Cria e apresenta um convite sem usar o modelo. |
| `/convites` | Lista convites pendentes, aceitos, expirados e revogados. |
| `/revogar` | Apresenta convites pendentes para revogação. |
| `/minhaconta` | Mostra perfil e canais vinculados da própria conta. |
| `/ajuda` | Mostra capacidades e comandos. |
| `/contas` | Lista contas ativas e desativadas; somente administrador. |
| `/desativarconta ID` | Mostra a confirmação; a mesma chamada com `confirmar` suspende sem apagar dados. |
| `/reativarconta ID` | Restaura uma conta suspensa e seus canais anteriormente ativos. |

Pedidos equivalentes em linguagem natural podem usar tools tipadas:

```text
create_user_invite
list_user_invites
revoke_user_invite
get_my_account
```

Os comandos determinísticos devem ser avaliados antes do runtime do agente para reduzir latência e
evitar gasto de modelo em operações administrativas simples.

## Rotas HTTP equivalentes

Manter casos de uso acessíveis também para uma futura interface autenticada:

```text
POST   /v1/invites/telegram
GET    /v1/invites/telegram
DELETE /v1/invites/telegram/{invite_id}
GET    /v1/account
```

As rotas usam o mesmo serviço chamado por commands/tools. Nenhuma rota aceita `user_id` ou
`workspace_id` no payload.

## Ausência de quotas

A implementação não deve criar nem aplicar:

- quantidade máxima de mensagens por dia ou mês;
- quantidade máxima de convites ativos ou históricos;
- saldo de tokens por conta;
- bloqueio por volume de tools;
- período de teste;
- diferenciação de capacidades entre convidante e convidado;
- cobrança, plano ou paywall.

As contas continuam sujeitas apenas às limitações técnicas dos provedores e aos limites globais de
segurança já existentes em cada turno do agente, como tamanho de entrada, passos máximos de function
calling e timeout. Falha de provedor deve produzir retry observável, nunca consumir uma quota local.

## Isolamento e autorização

- O workspace novo é criado com `owner_user_id` da nova conta.
- Nenhum registro do workspace do convidante é copiado.
- O convite não contém IDs internos.
- Tools recebem o `RequestContext` resolvido pelo canal.
- Toda consulta continua filtrada por `workspace_id`.
- O administrador pode revogar convites ainda pendentes e suspender ou reativar contas pelos
  comandos dedicados, sem acessar o conteúdo do workspace pessoal.
- O papel de administrador vem de `platform_admins` e da allowlist de bootstrap
  `PLATFORM_ADMIN_USER_IDS`; ele nunca é inferido por um modelo.
- Uma conta aceita não compartilha workspace nem dados com o convidante; somente o administrador da
  plataforma pode suspender seu acesso.
- Auditoria registra criação, aceite, revogação, suspensão e reativação sem conteúdo de mensagens.

## Conta e recuperação

A conta criada pelo Telegram é completa mesmo sem e-mail. A identidade Telegram é suficiente para
usar o bot.

Como evolução, `/minhaconta` poderá oferecer **Vincular login web**, criando uma sessão curta que
associa uma identidade Supabase à mesma `app_users.id`. Essa associação não deve criar outro
workspace nem migrar dados. Perda ou exclusão da conta Telegram sem uma segunda identidade vinculada
deve ser informada como risco ao usuário.

## Observabilidade

Registrar métricas e eventos sem quotas ou bloqueios automáticos:

- convites criados, aceitos, expirados e revogados;
- tempo entre criação e aceite;
- falhas da RPC de aceite;
- replays tratados idempotentemente;
- contas criadas e identidades vinculadas;
- falhas de envio da mensagem de boas-vindas.

Não registrar:

- token ou deep link completo;
- conteúdo de mensagens administrativas;
- username como identificador de autoridade;
- dados do convidado nos logs do convidante.

## Implementação

### Etapa 1 — identidade interna e migration — concluída

- Criar `app_users`, `user_identities` e `channel_invites`.
- Fazer backfill dos usuários e canais atuais preservando UUIDs.
- Adicionar models SQLAlchemy e constraints.
- Adaptar autenticação Supabase para resolver identidade externa.

**Gate:** usuário existente mantém acesso ao mesmo workspace e nenhum dado muda de proprietário.

### Etapa 2 — domínio e administração de convites — concluída

- Implementar criação, listagem e revogação autorizadas por `platform_admins`.
- Gerar token forte e persistir somente o hash.
- Criar serializadores seguros.
- Registrar auditoria.

**Gate:** token não aparece no banco após a resposta de criação; outro workspace não consegue listar
ou revogar o convite.

### Etapa 3 — aceite transacional — concluída

- Criar a RPC `accept_telegram_invite`.
- Implementar criação da conta, identidade, workspace e canal na mesma transação.
- Tratar concorrência e replay.

**Gate:** duas tentativas concorrentes nunca criam duas contas nem dois workspaces para a mesma
identidade Telegram.

### Etapa 4 — onboarding no webhook — concluída no código

- Reconhecer o payload `invite_`.
- Chamar a RPC e mapear códigos seguros.
- Enviar boas-vindas ou erro genérico.
- Implementar ajuda para `/start` sem convite.

**Gate:** o token não chega ao agente nem a `channel_messages`; após o aceite, a primeira mensagem
normal cria uma única conversa.

### Etapa 5 — commands, tools e rotas — concluída no código

- Adicionar `/convidar`, `/convites`, `/revogar` e `/minhaconta`.
- Registrar tools equivalentes no catálogo.
- Adicionar rotas HTTP sobre os mesmos casos de uso.
- Atualizar prompt de capacidades sem expor detalhes internos.

**Gate:** comandos administrativos não chamam o modelo; pedidos em linguagem natural selecionam a
tool correta e respeitam o workspace.

### Etapa 6 — conta e exclusão — parcial

- Visualização da própria conta implementada; alteração de perfil ainda pendente.
- Implementar exclusão de conta com confirmação R2.
- Cancelar jobs pendentes e tornar dados indisponíveis antes da remoção derivada.
- Preservar auditoria mínima conforme a política de retenção.

**Gate:** o convidante não consegue excluir a conta aceita; somente o próprio usuário autenticado
pode iniciar e confirmar a exclusão.

### Etapa 7 — publicação e piloto — em andamento

- Migration aplicada no Supabase; manter a revisão alinhada ao código publicado.
- Publicar a nova Edge Function.
- Manter `allowed_updates=["message"]` enquanto não houver botões de callback.
- Executar smoke test com uma segunda conta real do Telegram.
- Confirmar API, worker, webhook e outbox saudáveis.

**Gate:** convite real cria conta isolada, mensagem real recebe resposta e nenhum dado do convidante
aparece na conta nova.

## Testes obrigatórios

### Domínio e banco

- criação persiste somente hash;
- convite aceita exatamente uma identidade;
- convite expirado e revogado não cria conta;
- replay pela mesma identidade retorna sucesso idempotente;
- replay por identidade diferente retorna `unavailable`;
- concorrência não duplica usuário, workspace ou canal;
- identidade Telegram existente reutiliza a conta;
- backfill preserva todos os workspaces atuais;
- convidado recebe papel `owner` no próprio workspace;
- nenhuma regra de quota existe ou bloqueia uso.

### Segurança

- outro workspace não lista nem revoga convite alheio;
- token não aparece em logs, auditoria ou respostas posteriores;
- grupo, bot ou update sem segredo é rejeitado/ignorado;
- username alterado não muda a identidade;
- modelo não recebe token, `user_id` ou `workspace_id`;
- convidante não acessa a conta criada.

### Ponta a ponta

- `/convidar` gera link compartilhável;
- deep link cria conta e envia boas-vindas;
- comando de aceite não entra no histórico;
- primeira mensagem normal passa por agente e outbox;
- mensagem repetida não executa o agente duas vezes;
- `/convites` reflete aceite e revogação;
- conta criada não pode gerar convites enquanto a política for `admin_only`.

## Critérios de aceite

1. Uma pessoa sem conta só entra por convite válido.
2. O aceite cria ou reutiliza uma conta interna completa.
3. A conta possui workspace pessoal e papel `owner`.
4. Dados do convidante e do novo usuário permanecem totalmente isolados.
5. Convite é consumido atomicamente e replay é idempotente.
6. O token nunca é persistido em texto puro nem enviado ao modelo.
7. O usuário pode conversar imediatamente depois do aceite.
8. A conta convidada tem as mesmas capacidades de qualquer outra conta.
9. Não existe quota local de mensagens, convites, tokens, tools ou duração de uso.
10. Somente o dono da própria conta pode alterá-la ou excluí-la.
11. Somente um administrador explícito pode criar convites enquanto a política for `admin_only`.

## Mapa esperado de arquivos

| Componente | Local sugerido |
| --- | --- |
| Models de usuário e convite | `src/agents_backend/models.py` |
| Contratos HTTP | `src/agents_backend/schemas.py` |
| Identidades | `src/agents_backend/auth.py` |
| Casos de uso de convite | `src/agents_backend/invitations/service.py` |
| Rotas | `src/agents_backend/api/invitation_routes.py` |
| Commands do Telegram | `src/agents_backend/conversation/telegram_commands.py` |
| Tools administrativas | `src/agents_backend/conversation/tools.py` |
| Webhook público | `supabase/functions/telegram-webhook/` |
| RPC e tabelas | nova migration Alembic posterior a `20260816_0002` |
| Testes Python | `tests/test_invitations.py` e `tests/test_auth.py` |
| Testes Edge | `supabase/functions/telegram-webhook/core_test.ts` |

## Fora deste guia

- compartilhamento do workspace do convidante;
- múltiplos membros no mesmo workspace;
- papéis além de `owner`;
- grupos e canais do Telegram;
- monetização, planos, cobrança ou quotas;
- painel administrativo de conteúdo;
- automações externas, e-mail e calendário;
- login web obrigatório durante o convite.
