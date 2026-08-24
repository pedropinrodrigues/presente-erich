# Evolução após o MVP

## Condição para avançar

Não adicionar automação antes de a memória provar utilidade: recuperação correta, fontes compreensíveis, baixa taxa de duplicação e correções fáceis para o usuário. Métricas de volume não substituem esses sinais.

## Próximas fases

| Fase | Estado | Adições | Dependência |
| --- | --- | --- | --- |
| Interface conversacional | Implementada | Runtime de agente, tools da memória, confirmações e endpoint de teste independente de canal. | Piloto controlado e observabilidade. |
| Canal Telegram | Gateway publicado | Bot, Edge Function, segredo, webhook idempotente, deep link, worker e outbox. | Executar e observar o piloto real. |
| Agentes em duas camadas | Implementada | Agente rápido Luna, tarefas persistidas e orquestrador Terra. | Observar piloto e ampliar tools por capacidade. |
| Backend permanente | Especificado | API e worker always-on no Northflank, mantendo Supabase como banco, Auth e gateway. | Implementar a [spec de transição](13-backend-hosting-transition-spec.md). |
| Convites pelo Telegram | Especificado | Conta interna, workspace pessoal, convite compartilhável e aceite atômico. | Implementar [o guia de convites](10-telegram-invites-and-accounts.md). |
| Canal WhatsApp | Adaptador preservado | Cloud API, assinatura HMAC, vínculo e normalização implementados. | Ativar quando houver número empresarial brasileiro. |
| Assistente executivo | Planejado | Calendário, briefings e acompanhamento de pendências. | Qualidade confiável de memória e retrieval. |
| Ações assistidas | Planejado | Rascunhos, aprovações e integrações somente de baixo risco. | Políticas de autorização e auditoria maduras. |
| Agente pessoal | Planejado | Skills, integrações de e-mail/mensageria e autonomia progressiva. | Confiança do usuário, controles e métricas operacionais. |

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
