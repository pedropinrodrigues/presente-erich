# Voz no Telegram com AssemblyAI Universal-2

## Objetivo

Permitir que uma mensagem de voz privada do Telegram seja tratada como uma entrada normal da Luna,
sem armazenar o arquivo de áudio no banco e sem transformar o agente conversacional em um cliente
de mídia. A transcrição usa o modelo pré-gravado `universal-2` da AssemblyAI.

## Fluxo

```text
Telegram voice
  → Supabase Edge Function valida e persiste metadados
  → channel_messages: transcription_pending
  → audio_transcription_jobs: upload
  → worker baixa o arquivo pela Bot API
  → AssemblyAI: upload → submit universal-2 → poll
  → channel_messages: received + texto transcrito
  → Luna decide answer / clarify / delegate
  → outbox → Telegram
```

Cada etapa externa é persistida antes da próxima. Se o processo reiniciar, o worker retoma a partir
de `stage` (`upload`, `submit` ou `poll`). O identificador externo da mensagem e a restrição única de
`channel_message_id` impedem transcrições duplicadas.

## Ordenação e experiência

Enquanto houver um áudio anterior em `transcription_pending`, mensagens posteriores da mesma
conversa não são reivindicadas pelo agente. O worker envia `typing` ao iniciar o download. Ao final,
a resposta usa o mesmo fluxo de texto; não há uma mensagem intermediária fixa.

Se a confiança ficar abaixo de `ASSEMBLYAI_MIN_CONFIDENCE`, a mensagem recebe
`transcription_low_confidence=true`. A Luna pede uma clarificação curta antes de delegar quando a
incerteza atingir nomes, datas, destinatários ou outra ação com consequência.

## Configuração

```dotenv
ASSEMBLY_AI_API_TOKEN=
ASSEMBLYAI_API_BASE_URL=https://api.assemblyai.com
ASSEMBLYAI_MODEL=universal-2
ASSEMBLYAI_LANGUAGE_CODE=pt
ASSEMBLYAI_TIMEOUT_SECONDS=30
ASSEMBLYAI_POLL_INTERVAL_SECONDS=1
ASSEMBLYAI_MAX_AUDIO_SECONDS=120
ASSEMBLYAI_MAX_AUDIO_BYTES=20000000
ASSEMBLYAI_MIN_CONFIDENCE=0.65
```

O alias legado `ASSEMBLYAI_API_KEY` também é aceito, mas o nome canônico do projeto é
`ASSEMBLY_AI_API_TOKEN`.

## Falhas e privacidade

- arquivos maiores ou áudios com mais de dois minutos são recusados antes do upload quando o
  Telegram fornece os metadados;
- falhas temporárias usam backoff e respeitam `WORKER_MAX_ATTEMPTS`;
- falha terminal produz uma única resposta pela outbox pedindo novo áudio ou texto;
- o áudio não é gravado em disco nem no PostgreSQL;
- texto, URL temporária e credenciais não são escritos em logs;
- depois do sucesso, o worker solicita a exclusão da transcrição na AssemblyAI e remove a URL de
  upload persistida.

## Validação

Os testes cobrem parsing de voz no Python e na Edge Function, download pela Telegram Bot API,
seleção explícita do Universal-2, retomada das três etapas, conversão da mensagem para `received`,
limpeza remota, idempotência e barreira de ordenação. O smoke test final é enviar um áudio privado
curto em português ao bot e conferir `audio_transcription_jobs`, a resposta e os logs sanitizados.
