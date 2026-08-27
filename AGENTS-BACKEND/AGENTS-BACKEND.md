# Agents & Backend — MVP

## Propósito

Agents & Backend é o núcleo que recebe transcrições, preserva a fonte, cria memória útil e permite
que um agente conversacional consulte ou altere essa memória por tools controladas. Este documento
define o MVP funcional; não é uma especificação da arquitetura final.

## Fluxo que precisa funcionar

```text
Transcript Event → fonte persistida → extração estruturada
→ memória consolidada → busca → resposta com evidências

Mensagem → agente conversacional → tool autorizada → serviço de domínio
→ feedback no mesmo canal

Conversas concluídas do dia → fechamento diário idempotente
→ fonte diária → extração conservadora → memória de longo prazo
```

Transcrições podem chegar prontas pela API ou ser produzidas a partir de mensagens de voz privadas
do Telegram. Nesse caso, o backend baixa o arquivo temporariamente, usa AssemblyAI Universal-2 e
libera apenas o texto transcrito para o fluxo conversacional.

## Escopo do MVP

- receber `Transcript Events` de forma autenticada e idempotente;
- armazenar a transcrição original e seu estado de processamento;
- extrair pessoas, organizações, projetos, decisões, compromissos e fatos;
- relacionar informação nova a entidades existentes quando houver confiança suficiente;
- manter fatos com fonte, tempo, confiança e status;
- buscar por texto, filtros estruturados e similaridade quando disponível;
- responder perguntas usando fontes recuperáveis;
- permitir correção e exclusão auditáveis pelo usuário.
- receber turnos conversacionais autenticados e idempotentes;
- mediar leitura e escrita por um catálogo fechado de tools;
- exigir confirmação em outro turno para toda exclusão;
- receber texto ou voz do Telegram Bot API por webhook e responder por outbox.
- consolidar diariamente as conversas reais de cada usuário para ingestão seletiva na memória.

Grupos, canais e mídias do Telegram diferentes de mensagens de voz permanecem fora do escopo.

## Módulos

| Módulo | Responsabilidade |
| --- | --- |
| API | Autenticação, validação de contrato e casos de uso. |
| Ingestion | Persistir fontes e coordenar o processamento assíncrono. |
| Extraction | Produzir candidatos estruturados a partir da transcrição. |
| Memory | Validar, deduplicar e versionar entidades, fatos e compromissos. |
| Retrieval | Recuperar contexto e evidências para uma pergunta. |
| Worker | Executar extração e indexação com retry. |
| Agente rápido | Responder consultas de memória e delegar tarefas sem tools de escrita. |
| Orquestrador | Executar tarefas persistidas com Terra e tools limitadas por capacidade. |
| Tool Registry | Validar argumentos, política, idempotência e execução de casos de uso. |
| Telegram adapter | Verificar webhook, normalizar texto/voz, vincular identidade e usar outbox. |
| Audio transcription | Processar voz em fila durável com AssemblyAI Universal-2. |
| Daily conversation memory | Consolidar o diálogo diário e criar jobs idempotentes de extração. |

## Decisões de implementação do MVP

- **Linguagem e API:** Python com FastAPI.
- **IA:** SDK Python oficial da OpenAI e Responses API, com Structured Outputs para extração.
- **Orquestração:** workflows explícitos no código; LangGraph ou LangChain só serão adotados se uma etapa futura demonstrar uma necessidade concreta.
- **Supabase hospedado:** um único projeto para Auth, PostgreSQL e a Edge Function pública do
  webhook Telegram.
- **Execução da aplicação:** API e worker Python podem rodar localmente ou em serviços separados no
  Northflank. Docker, provisionamento, migration job, health/readiness, heartbeat e canários estão
  implementados; a conclusão operacional de cada rollout depende dos smokes externos documentados.

## Documentação por assunto

- [Arquitetura do MVP](docs/01-mvp-architecture.md)
- [Ingestão e memória](docs/02-ingestion-and-memory.md)
- [API e casos de uso](docs/03-api-and-use-cases.md)
- [Desenvolvimento local e banco](docs/04-local-development-and-database.md)
- [Qualidade e avaliação](docs/05-quality-and-evaluation.md)
- [Seleção de modelos e custo](docs/07-model-selection-and-cost.md)
- [Evolução após o MVP](docs/06-post-mvp-roadmap.md)
- [Agente conversacional e tools](docs/08-whatsapp-agent-tools-plan.md)
- [Agente rápido e orquestrador de tarefas](docs/09-orchestrated-agent-architecture.md)
- [Convites e contas pelo Telegram](docs/10-telegram-invites-and-accounts.md)
- [Integrações externas pelo Composio MCP](docs/11-composio-mcp-gateway-integration-plan.md)
- [Agendamentos e rotinas](docs/12-scheduled-automations.md)
- [Transição do backend para hospedagem permanente](docs/13-backend-hosting-transition-spec.md)
- [Voz no Telegram com AssemblyAI](docs/14-telegram-voice-transcription.md)

## Critérios de aceite

1. Um `capture_id` repetido não cria nova fonte nem nova memória.
2. A fonte textual original é preservada e acessível como evidência.
3. Uma transcrição pode gerar entidades, fatos e compromissos revisáveis.
4. Perguntas retornam resposta, evidências e incertezas relevantes.
5. Correções e exclusões passam pelo domínio e ficam registradas.
6. Falhas temporárias de processamento podem ser repetidas sem corromper dados.
7. Replays de mensagens ou tools não repetem efeitos concluídos.
8. Nenhuma exclusão via agente ocorre sem confirmação válida em um segundo turno.

## Princípios inegociáveis

- A IA sugere; serviços de domínio validam e persistem.
- Fonte e histórico não são sobrescritos silenciosamente.
- Dados são isolados por usuário ou workspace.
- A interface e a tecnologia de captura não definem a lógica de memória.

O contexto compartilhado do produto está em [`../CONTEXT-PACK.md`](../CONTEXT-PACK.md).
