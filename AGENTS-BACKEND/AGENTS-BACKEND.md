# Agents & Backend — MVP

## Propósito

Agents & Backend é o núcleo que recebe transcrições, preserva a fonte, cria memória útil e responde perguntas com evidências. Este documento define somente o MVP funcional; não é uma especificação da arquitetura final.

## Fluxo que precisa funcionar

```text
Transcript Event → fonte persistida → extração estruturada
→ memória consolidada → busca → resposta com evidências
```

O áudio nunca chega ao backend. A transcrição e seus metadados são a fonte persistente do MVP.

## Escopo do MVP

- receber `Transcript Events` de forma autenticada e idempotente;
- armazenar a transcrição original e seu estado de processamento;
- extrair pessoas, organizações, projetos, decisões, compromissos e fatos;
- relacionar informação nova a entidades existentes quando houver confiança suficiente;
- manter fatos com fonte, tempo, confiança e status;
- buscar por texto, filtros estruturados e similaridade quando disponível;
- responder perguntas usando fontes recuperáveis;
- permitir correção e exclusão auditáveis pelo usuário.

Ficam fora do MVP: envio de mensagens, Skills de escrita, automações, calendário, briefings, notificações proativas, múltiplos agentes autônomos e loops agênticos abertos.

## Módulos

| Módulo | Responsabilidade |
| --- | --- |
| API | Autenticação, validação de contrato e casos de uso. |
| Ingestion | Persistir fontes e coordenar o processamento assíncrono. |
| Extraction | Produzir candidatos estruturados a partir da transcrição. |
| Memory | Validar, deduplicar e versionar entidades, fatos e compromissos. |
| Retrieval | Recuperar contexto e evidências para uma pergunta. |
| Worker | Executar extração e indexação com retry. |

## Decisões de implementação do MVP

- **Linguagem e API:** Python com FastAPI.
- **IA:** SDK Python oficial da OpenAI e Responses API, com Structured Outputs para extração.
- **Orquestração:** workflows explícitos no código; LangGraph ou LangChain só serão adotados se uma etapa futura demonstrar uma necessidade concreta.
- **Banco hospedado:** um único projeto Supabase para Auth e PostgreSQL.
- **Execução da aplicação:** API e worker rodam localmente neste momento. Deploy da aplicação, staging e infraestrutura de escala ficam para depois do MVP funcional.

## Documentação por assunto

- [Arquitetura do MVP](docs/01-mvp-architecture.md)
- [Ingestão e memória](docs/02-ingestion-and-memory.md)
- [API e casos de uso](docs/03-api-and-use-cases.md)
- [Desenvolvimento local e banco](docs/04-local-development-and-database.md)
- [Qualidade e avaliação](docs/05-quality-and-evaluation.md)
- [Seleção de modelos e custo](docs/07-model-selection-and-cost.md)
- [Evolução após o MVP](docs/06-post-mvp-roadmap.md)
- [Guia de desenvolvimento](DEVELOPMENT-GUIDE/DEVELOPMENT-GUIDE.md)

## Critérios de aceite

1. Um `capture_id` repetido não cria nova fonte nem nova memória.
2. A fonte textual original é preservada e acessível como evidência.
3. Uma transcrição pode gerar entidades, fatos e compromissos revisáveis.
4. Perguntas retornam resposta, evidências e incertezas relevantes.
5. Correções e exclusões passam pelo domínio e ficam registradas.
6. Falhas temporárias de processamento podem ser repetidas sem corromper dados.

## Princípios inegociáveis

- A IA sugere; serviços de domínio validam e persistem.
- Fonte e histórico não são sobrescritos silenciosamente.
- Dados são isolados por usuário ou workspace.
- A interface e a tecnologia de captura não definem a lógica de memória.

O contexto compartilhado do produto está em [`../CONTEXT-PACK.md`](../CONTEXT-PACK.md).
