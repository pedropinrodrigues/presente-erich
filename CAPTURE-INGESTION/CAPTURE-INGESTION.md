# Capture & Ingestion — Especificação da captura

## 1. Propósito

Esta camada torna o registro por voz simples, privado e confiável. No MVP, ela roda no iPhone: grava áudio temporariamente, faz a transcrição no dispositivo e envia somente texto e metadados ao backend.

Ela não interpreta o significado do conteúdo. Sua responsabilidade termina quando o backend confirma que recebeu a transcrição de forma durável.

## 2. Responsabilidades

- iniciar e encerrar uma captura com baixa fricção;
- manter áudio e texto temporários protegidos no dispositivo;
- transcrever localmente;
- criar metadados de captura quando disponíveis;
- validar falhas inequívocas antes do envio;
- persistir uma fila local de eventos pendentes;
- enviar eventos de modo idempotente e repetir tentativas;
- excluir o áudio apenas após confirmação de entrega.

Não é responsabilidade desta camada classificar entidades, decidir relevância, atualizar memória, responder perguntas ou executar ações externas.

## 3. Contrato com Agents & Backend

O contrato de fronteira é um `Transcript Event`. Todos os dispositivos e canais devem produzir o mesmo formato conceitual:

```json
{
  "capture_id": "uuid",
  "source": "iphone",
  "captured_at": "2026-08-13T10:15:00-03:00",
  "duration_seconds": 642,
  "language": "pt-BR",
  "transcript": "Conversei hoje com Carlos sobre o Projeto Alfa...",
  "metadata": {}
}
```

| Campo | Regra |
| --- | --- |
| `capture_id` | Obrigatório, único e estável entre retries. |
| `source` | Identifica o canal de origem; não muda a lógica central de Agents. |
| `captured_at` | Data/hora da captura com fuso quando disponível. |
| `transcript` | Obrigatório para envio; representa a fonte textual persistente. |
| Demais metadados | Opcionais, mas enviados quando disponíveis e confiáveis. |

O backend responde sucesso somente depois da persistência durável. A confirmação não depende de extração, indexação ou consolidação de memória.

## 4. Fluxo e estados

```text
Gravando → Áudio temporário → Transcrição local → Validação básica
→ Texto pendente em fila → Enviado → Confirmado → Áudio excluído
```

- Sem transcrição válida: manter o áudio temporário ou apresentar uma falha recuperável.
- Falha de rede ou ausência de confirmação: manter o texto na fila e tentar novamente com o mesmo `capture_id`.
- Após existir texto válido, retries usam o texto salvo; não exigem nova transcrição.
- Após confirmação durável: marcar a captura concluída e excluir o áudio local.

## 5. Validation Gate

O dispositivo descarta somente falhas inequívocas, como áudio corrompido ou transcrição vazia. Pode checar presença de texto, integridade básica dos metadados e coerência técnica da gravação.

Não deve descartar conteúdos curtos, ambíguos ou aparentemente pouco relevantes. Uma frase breve pode ser uma decisão ou lembrete importante; relevância é avaliada pelo backend.

## 6. Privacidade e segurança

- Áudio nunca é enviado ou armazenado pelo backend no fluxo normal.
- Áudio e textos temporários usam as proteções nativas disponíveis no iPhone.
- Nenhum outro aplicativo acessa esse material sem autorização do usuário.
- A retenção do áudio termina após confirmação de entrega; o texto pendente existe apenas para garantir retry.
- Logs e telemetria não devem incluir a transcrição completa, salvo fluxo explicitamente autorizado e protegido.

## 7. MVP e evolução

O MVP pode usar Atalho, widget ou Action Button, desde que a experiência seja iniciar, falar e encerrar. A escolha concreta da interface de captura permanece aberta.

No futuro, Omi, Plaud, desktop, bots de reunião e outros canais devem ser implementados como adaptadores que produzem o mesmo `Transcript Event`, sem modificar Agents.

## 8. Critérios de pronto

- Captura válida gera um evento único e reenviável.
- Uma repetição do evento não duplica a captura no backend.
- Queda de conexão não perde texto já transcrito.
- Áudio não é apagado antes da confirmação durável.
- Áudio é removido após confirmação.
- O backend nunca precisa receber áudio para processar a captura.
