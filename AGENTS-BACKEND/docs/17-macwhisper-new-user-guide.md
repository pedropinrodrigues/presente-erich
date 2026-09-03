# Guia do novo usuário — Telegram e MacWhisper

## Objetivo

Este guia explica como um novo usuário cria sua conta pessoal pelo convite do Telegram e prepara o
MacWhisper para enviar transcrições ao agente.

> Estado em 03/09/2026: cadastro, comandos, adaptador e migration `20260903_0012` publicados em
> produção na revisão `3c5c6d8`. API e worker estão ativos no Northflank.
> Não use `/v1/transcripts` como Webhook URL, pois esse endpoint possui outro contrato.

## Antes de começar

O novo usuário precisa de:

- aplicativo Telegram com acesso ao chat privado do bot;
- MacWhisper Pro 13.6 ou superior;
- uma URL pessoal de webhook gerada pelo comando `/macwhisper`;
- conexão HTTPS para enviar as transcrições ao backend.

O convite do Telegram e a futura URL do webhook são credenciais diferentes. Não publique, não
encaminhe para terceiros e não salve nenhuma delas neste repositório.

## 1. Criar a conta pelo convite do Telegram

1. Abra o link de convite recebido diretamente do administrador.
2. O Telegram abrirá o chat privado do bot.
3. Toque em **Start/Iniciar**.
4. Aguarde a mensagem confirmando que a conta foi criada e vinculada.
5. Envie `/minhaconta` para conferir que a conta está ativa.

Cada convite:

- pode ser usado uma única vez;
- expira no prazo informado pelo administrador;
- cria uma conta e um workspace pessoais;
- não concede acesso ao workspace de quem criou o convite.

Se o bot disser que o convite expirou, foi revogado ou já foi utilizado, solicite um novo link ao
administrador. Não tente editar manualmente o parâmetro `start` do link.

## 2. Preparar o MacWhisper

1. Atualize o MacWhisper.
2. Confirme que a licença Pro está ativa.
3. Abra **MacWhisper → Settings/Preferences → Integrations**.
4. Localize **Custom Webhook**.
5. Mantenha **Automatically send after finished transcription** desligado até receber e testar a
   URL pessoal.

Segundo a documentação atual do MacWhisper, o Custom Webhook envia `POST` com
`Content-Type: application/json` e este corpo:

```json
{
  "title": "Título da transcrição",
  "transcript": "Texto completo da transcrição"
}
```

O payload não inclui necessariamente `file_name`, `duration`, `language` ou `segments`. O adaptador
do backend será responsável por gerar identificador idempotente, horário de captura, origem e
metadados internos.

Referência oficial:
[Integrating MacWhisper with other services](https://docs.macwhisper.com/article/53-integrating-macwhisper-with-other-services).

## 3. Configurar a URL pessoal

### Onde o usuário pegará o segredo pessoal

O segredo não será criado pelo administrador nem enviado por e-mail. O próprio usuário deverá abrir
o chat privado do bot e enviar:

```text
/macwhisper
```

O backend verificará a conta vinculada, criará um segredo aleatório exclusivo e responderá uma
única vez com a URL completa:

```text
https://API_PUBLICA/v1/integrations/macwhisper/webhooks/SEGREDO_PESSOAL
```

O segredo estará embutido na URL. Não haverá um segundo campo para copiar e ele não será o token do
convite do Telegram, JWT do Supabase ou token do Bitrix24. O banco armazenará somente o hash do
segredo.

Para invalidar a URL e impedir novos envios, o usuário deverá enviar:

```text
/revogarmacwhisper
```

Depois da revogação, `/macwhisper` gerará uma nova URL; a URL anterior deixará de funcionar e deverá
ser substituída no aplicativo.

> **Disponível em produção:** não use o token do convite ou `/v1/transcripts` como alternativa. A
> única credencial correta é a URL pessoal devolvida pelo comando `/macwhisper`.

### Configuração no aplicativo

A URL terá formato semelhante a:

```text
https://API_PUBLICA/v1/integrations/macwhisper/webhooks/SEGREDO_PESSOAL
```

1. Copie a URL completa pelo canal seguro fornecido pelo sistema.
2. Cole em **Integrations → Custom Webhook → Webhook URL**.
3. Clique em **Test**.
4. Confirme que o MacWhisper mostra **Success**.
5. Faça uma transcrição curta sem informação sensível.
6. Confirme no agente que a transcrição foi recebida.
7. Ative **Automatically send after finished transcription** se quiser o envio automático.

Não coloque nessa tela:

- o link de convite do Telegram;
- token do Bitrix24;
- token JWT do Supabase;
- chave da OpenAI;
- a rota genérica `/v1/transcripts`.

## 4. Privacidade e revogação

Quando o envio automático estiver ativo, cada transcrição concluída deixará o Mac e será enviada ao
backend. Revise o conteúdo antes de habilitar esse comportamento para reuniões confidenciais.

Se a URL pessoal vazar:

1. desligue o envio automático no MacWhisper;
2. peça a revogação da URL comprometida;
3. gere uma URL nova;
4. substitua a URL no MacWhisper e use **Test** novamente.

O bot exibe o segredo uma única vez no chat privado. Depois da entrega, o backend substitui a URL
persistida por um marcador redigido, exclui essa mensagem do contexto enviado ao modelo e mantém
somente o hash da credencial. O segredo não aparece em listagens ou arquivos versionados.

## 5. Solução rápida de problemas

| Sintoma | O que verificar |
| --- | --- |
| A aba Integrations está bloqueada | Confirme a licença MacWhisper Pro e atualize o aplicativo. |
| O botão Test não aparece | Verifique se a URL começa com `https://` e está completa. |
| `Not Found` | A rota ainda não foi publicada, a URL está incorreta ou a credencial foi revogada. Gere outra com `/macwhisper` se necessário. |
| `Invalid request` | O aplicativo pode estar enviando outro payload; registre a versão e procure suporte. |
| Test funciona, mas nada é enviado | Ative **Automatically send after finished transcription**. |
| Transcrição duplicada | Não reenvie; informe título e horário ao suporte para verificar a idempotência. |

## Checklist final

- [ ] Conta criada pelo convite do Telegram.
- [ ] `/minhaconta` confirma a conta ativa.
- [ ] MacWhisper Pro atualizado.
- [ ] O adaptador MacWhisper e os comandos do bot foram publicados.
- [ ] `/macwhisper` gerou a URL pessoal no chat privado do usuário.
- [ ] URL pessoal de webhook recebida pelo comando `/macwhisper`.
- [ ] Teste do Custom Webhook mostra **Success**.
- [ ] Transcrição curta aparece no agente.
- [ ] Envio automático ativado somente após o teste.

## Responsabilidades

| Pessoa/sistema | Responsabilidade |
| --- | --- |
| Administrador | Criar e compartilhar somente o convite de cadastro do Telegram. |
| Novo usuário | Aceitar o convite, executar `/macwhisper` e configurar a URL recebida. |
| Backend | Gerar o segredo, guardar apenas seu hash, receber o webhook e permitir revogação. |
| MacWhisper | Enviar `title` e `transcript` para a URL configurada. |
