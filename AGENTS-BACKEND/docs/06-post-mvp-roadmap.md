# Evolução após o MVP

## Condição para ampliar autonomia

Automação limitada já foi adicionada. Novas tools, efeitos permanentes e maior autonomia só devem
avançar quando memória e execução provarem utilidade: recuperação correta, fontes compreensíveis,
baixa duplicação, correções fáceis, confirmação confiável e ausência de efeitos repetidos. Métricas
de volume não substituem esses sinais.

## Próximas fases

| Fase | Estado | Adições | Dependência |
| --- | --- | --- | --- |
| Interface conversacional | Implementada | Runtime de agente, tools da memória, confirmações e endpoint de teste independente de canal. | Piloto controlado e observabilidade. |
| Canal Telegram | Gateway publicado | Bot, Edge Function, segredo, webhook idempotente, deep link, worker e outbox. | Executar e observar o piloto real. |
| Agentes em duas camadas | Implementada | Agente rápido Luna, tarefas persistidas e orquestrador Terra. | Observar piloto e ampliar tools por capacidade. |
| Backend permanente | Implementado no repositório | API e worker no Northflank, mantendo Supabase como banco, Auth e gateway. | Validar revisão, health, filas e canários da [spec de transição](13-backend-hosting-transition-spec.md). |
| Convites pelo Telegram | Implementado; piloto pendente | Conta interna, workspace pessoal, convite compartilhável e aceite atômico. | Concluir canário real e itens parciais do [guia de convites](10-telegram-invites-and-accounts.md). |
| Canal WhatsApp | Adaptador preservado | Cloud API, assinatura HMAC, vínculo e normalização implementados. | Ativar quando houver número empresarial brasileiro. |
| Assistente executivo | Base implementada | Agendamentos, recorrências, Calendar, memória contextual e acompanhamento de pendências. | Validar briefings e rotinas no piloto hospedado. |
| Ações assistidas | Catálogo inicial implementado | Gmail, Calendar e WhatsApp Business com risco R0–R2 e confirmação. | Revogação, reconciliação e telemetria operacional maduras. |
| Agente pessoal | Base implementada | Memória diária, integrações e autonomia limitada por capabilities. | Piloto real, confiança do usuário, controles e métricas operacionais. |

## Ideias deliberadamente adiadas

- agentes autônomos sem fronteiras de capacidade;
- loops de planejamento sem limite;
- envio automático de e-mails ou mensagens;
- notificações proativas sem preferência explícita;
- microsserviços e infraestrutura de eventos dedicada;
- grafo de conhecimento especializado.

Essas opções podem ser reavaliadas quando existir uma necessidade mensurável que o MVP não resolva.

A implementação usa dois runtimes com fronteiras explícitas: o agente rápido consulta e delega; o
orquestrador executa somente capacidades autorizadas de uma tarefa persistida. Veja
[09-orchestrated-agent-architecture.md](09-orchestrated-agent-architecture.md).
