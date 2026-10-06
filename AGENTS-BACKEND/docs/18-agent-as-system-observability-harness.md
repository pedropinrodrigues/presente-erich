# Harness: o agente como interface do sistema

## Estado

Documento de direção e implementação incremental. Ele define o harness obrigatório para tornar o
chat a principal interface de uso, observabilidade, suporte e recuperação do produto sem conceder
ao modelo acesso irrestrito ao banco ou a dados de outros usuários.

Este documento deve ser consultado sempre que uma nova capacidade assíncrona, integração, rotina,
confirmação ou efeito externo for criada. Uma funcionalidade não está completa enquanto o usuário
não conseguir descobrir seu estado, entender o que aconteceu e iniciar uma recuperação segura pelo
próprio chat.

## Tese de produto

Quando o chat é a única porta do sistema, o agente precisa ser “quase o sistema” para o usuário.
Isso significa que ele deve representar, com fidelidade e dentro das permissões do usuário:

- o que o sistema sabe;
- o que está configurado;
- o que está em andamento;
- o que aconteceu;
- por que algo não produziu o resultado esperado;
- o que acontecerá em seguida;
- o que o usuário pode fazer para corrigir ou continuar.

O agente não deve fingir onisciência nem investigar tabelas livremente. O backend continua sendo a
fonte de verdade e expõe ferramentas de observabilidade pessoal com escopo, contratos e campos
permitidos. O modelo interpreta resultados estruturados e os apresenta em linguagem natural.

## Invariante principal

Para todo recurso ou operação pertencente ao usuário, o sistema deve conseguir responder:

1. O que é isso?
2. Qual é o estado atual?
3. O que aconteceu recentemente?
4. Qual foi a última etapa concluída?
5. Onde o fluxo parou?
6. Por que parou?
7. O resultado foi produzido?
8. O resultado foi entregue?
9. O que acontecerá em seguida?
10. O que o usuário pode fazer agora?

Se a causa não tiver sido registrada, a resposta correta é declarar a ausência de telemetria. O
agente nunca deve completar uma causa por plausibilidade.

## Fronteiras

### O agente pode

- consultar recursos do usuário atual;
- correlacionar estados persistidos pertencentes ao mesmo usuário e workspace;
- explicar códigos técnicos por meio de um catálogo determinístico;
- resolver referências naturais como “a rotina da EMBJ3” ou “meu último áudio”;
- sugerir recuperações já admitidas pela política do backend;
- iniciar uma recuperação somente após pedido explícito e confirmação quando necessária.

### O agente não pode

- consultar logs globais ou recursos de outro usuário;
- executar SQL, shell ou buscas arbitrárias em tabelas;
- retornar tokens, segredos, credenciais ou payloads sensíveis;
- inventar uma causa ausente;
- afirmar sucesso antes de existir estado terminal correspondente;
- transformar uma consulta de diagnóstico em mutação silenciosa;
- tratar saúde global da infraestrutura como se fosse informação pessoal do usuário.

## Arquitetura-alvo

```text
Mensagem do usuário
        │
        ▼
Luna classifica: conversa | consulta | diagnóstico | ação | confirmação
        │
        ▼
Tools de observabilidade pessoal (R0)
        │ ToolContext obrigatório
        ▼
UserObservabilityService
        │
        ├── resolvedores de referência natural
        ├── adaptador de rotinas
        ├── adaptador de transcrições e memória
        ├── adaptador de integrações e efeitos externos
        ├── adaptador de entregas
        └── catálogo determinístico de motivos
        │
        ▼
Resultado estruturado e sanitizado
        │
        ▼
Luna/Terra redige estado + causa + evidência + próximo passo
        │
        └── uma ação de recuperação usa a tool de mutação existente
```

O serviço de observabilidade é uma camada de leitura. As tabelas de domínio continuam sendo a fonte
de verdade. Uma projeção unificada de atividades poderá ser adicionada posteriormente, mas não é
pré-requisito para o primeiro corte vertical.

## Estrutura proposta no repositório

```text
src/agents_backend/user_observability/
├── __init__.py
├── schemas.py
├── reason_catalog.py
├── resolvers.py
├── service.py
├── tools.py
└── adapters/
    ├── schedules.py
    ├── ingestion.py
    ├── memory.py
    ├── delivery.py
    └── integrations.py
```

Responsabilidades:

- `schemas.py`: contratos comuns de recursos, estados, motivos, evidências e recuperações;
- `reason_catalog.py`: tradução determinística de códigos técnicos para explicações humanas;
- `resolvers.py`: resolução de nome, período, tipo e recência sem exigir UUID do usuário;
- `service.py`: escopo, correlação e composição das explicações;
- `tools.py`: tools R0 apresentadas ao agente;
- `adapters/`: leitura controlada das tabelas de cada domínio.

## Contrato canônico de explicação

Todos os adaptadores devem retornar o mesmo envelope conceitual:

```json
{
  "resource": {
    "kind": "schedule",
    "id": "internal-id",
    "label": "Resumo diário da EMBJ3"
  },
  "current_status": "expired",
  "summary": "A rotina não possui uma próxima execução.",
  "last_successful_stage": "update_prepared",
  "blocking_stage": "confirmation",
  "reason": {
    "code": "confirmation_timeout",
    "title": "A confirmação venceu",
    "detail": "A confirmação chegou depois do prazo registrado."
  },
  "last_activity_at": "2026-09-28T11:52:00-03:00",
  "next_expected_at": null,
  "production_status": "not_attempted",
  "delivery_status": "not_attempted",
  "pending_action": null,
  "timeline": [],
  "recoveries": [
    {
      "action": "reactivate_schedule",
      "label": "Preparar uma nova reativação",
      "risk": "R2",
      "requires_confirmation": true
    }
  ],
  "limitations": []
}
```

### Estados comuns

Os estados específicos de cada domínio devem ser normalizados para:

- `pending`: aceito, mas ainda não iniciado ou aguardando dependência;
- `awaiting_confirmation`: precisa de ação explícita do usuário;
- `running`: trabalho em andamento;
- `succeeded`: objetivo produzido com sucesso;
- `partially_succeeded`: parte do objetivo foi concluída;
- `failed`: terminou sem produzir o objetivo;
- `expired`: perdeu validade ou janela temporal;
- `paused`: interrompido de forma intencional e reversível;
- `cancelled`: cancelado pelo usuário ou por substituição;
- `needs_attention`: requer intervenção, mas ainda pode ser recuperado;
- `unknown`: não existe telemetria suficiente.

Produção e entrega devem ser estados separados. Uma pesquisa pode ser concluída e a mensagem falhar
na outbox; uma transcrição pode ser recebida e a extração de memória não terminar.

## Catálogo determinístico de motivos

O catálogo deve mapear códigos persistidos para quatro elementos:

```text
reason_code → título → explicação → recuperações permitidas
```

Primeiro conjunto obrigatório:

| Código | Explicação ao usuário | Recuperação típica |
| --- | --- | --- |
| `misfire_skipped` | O horário foi perdido e a política pulou a ocorrência. | Executar agora ou alterar política. |
| `confirmation_timeout` | A confirmação venceu antes da ativação. | Gerar nova confirmação. |
| `confirmation_too_late` | A janela útil da operação já passou. | Recriar com novo horário. |
| `scheduled_tool_failed` | Uma ferramenta necessária falhou durante a rotina. | Tentar novamente ou revisar conexão. |
| `pending_action_expired` | A ação não foi confirmada dentro do prazo. | Renovar confirmação quando permitido. |
| `integration_disconnected` | A conta externa não está mais autorizada. | Reconectar a conta. |
| `delivery_failed` | O conteúdo foi produzido, mas não foi entregue. | Reenviar quando seguro. |
| `max_attempts_exceeded` | As tentativas automáticas foram esgotadas. | Nova tentativa explícita. |
| código desconhecido | O sistema registrou a falha, mas não possui explicação pública. | Orientar suporte e registrar lacuna. |

As mensagens do catálogo não podem incluir stack traces, respostas brutas de provedores, tokens ou
identificadores que não ajudem o usuário.

## Resolução de referências naturais

O usuário não deve precisar copiar UUIDs. Toda tool de observabilidade deve aceitar um identificador
exato opcional e uma consulta natural alternativa.

Exemplos:

- “a rotina da EMBJ3”;
- “meu briefing das 17h”;
- “o último áudio que enviei”;
- “a reunião de ontem”;
- “o email que mandei para a Ana”;
- “a confirmação que venceu”.

Ordem de resolução:

1. recurso explicitamente associado ao turno ou à ação pendente;
2. correspondência exata por nome ou identificador público;
3. correspondência por termos e tipo de recurso;
4. correspondência por recência e contexto da conversa;
5. pedido curto de desambiguação quando restar mais de uma opção plausível.

O resolvedor deve retornar candidatos com rótulos e datas compreensíveis. O modelo não escolhe
silenciosamente entre candidatos ambíguos.

## Tools iniciais

### `explain_schedule`

Entrada:

```text
schedule_id opcional
query opcional
timeline_limit padrão 10
```

Correla:

- `ScheduledAutomation`;
- `ScheduledRun`;
- `ScheduleEvent`;
- `AutomationGrant`;
- `PendingAction`;
- `OrchestrationTask` e `OrchestrationTaskEvent`;
- `ToolExecution`;
- `OutboxMessage`.

Saída obrigatória:

- versão e estado atuais;
- eventual atualização pendente;
- prazo de confirmação;
- última execução e última execução bem-sucedida;
- estágio em que o fluxo parou;
- se houve produção do conteúdo;
- se houve tentativa de entrega;
- próxima execução;
- causa registrada;
- recuperações válidas.

### `list_schedule_activity`

Retorna uma linha do tempo curta, já sanitizada e ordenada, sem exigir que o modelo correlacione
eventos técnicos isolados.

### `list_my_activity`

Filtros:

```text
resource_kind opcional
status opcional
from_at opcional
to_at opcional
failures_only padrão false
limit padrão 20, máximo 100
```

### `explain_my_last_operation`

Resolve perguntas como:

- “o que aconteceu com meu último áudio?”;
- “aquilo foi salvo na memória?”;
- “o email foi enviado?”;
- “a reunião entrou na agenda?”;
- “por que não recebi o resumo?”.

### `get_my_capabilities`

Gera o catálogo de uso a partir das capabilities, permissões e integrações realmente ativas. O
onboarding, `/ajuda` e Luna devem usar esse resultado em vez de manter listas divergentes.

## Roteamento conversacional

Adicionar uma capacidade `user_observability` e, se necessário, uma intenção
`system_diagnostic`. Consultas de diagnóstico são R0 e não devem ser confundidas com pedidos de
alteração.

Luna deve reconhecer pelo menos:

- “por que não funcionou?”;
- “o que aconteceu?”;
- “isso foi enviado?”;
- “isso ficou salvo?”;
- “está ativo?”;
- “quando vai rodar de novo?”;
- “onde parou?”;
- “o que está pendente?”;
- “o que você consegue fazer na minha conta?”.

No primeiro corte, diagnósticos podem ser delegados ao Terra para permitir mais de uma leitura. As
consultas frequentes e inequívocas podem migrar depois para o fast path.

O agente deve responder sempre na ordem:

1. estado atual;
2. causa comprovada;
3. impacto;
4. próximo evento esperado;
5. opções de recuperação.

## Plano incremental de entrega

### Etapa 1 — contrato, escopo e catálogo

- criar `user_observability/schemas.py`;
- criar o catálogo inicial de motivos;
- implementar helpers de escopo por usuário e workspace;
- definir sanitização e allowlist de metadados;
- testar isolamento entre dois usuários.

**Gate:** nenhum adaptador consegue retornar recurso de outro usuário, mesmo recebendo seu UUID.

### Etapa 2 — corte vertical de rotinas

- implementar resolvedor de rotinas por ID, nome e recência;
- implementar `explain_schedule`;
- implementar `list_schedule_activity`;
- adicionar capability e tools ao orquestrador;
- ensinar Luna a rotear perguntas de diagnóstico;
- criar fixture reproduzindo o incidente da EMBJ3.

**Gate:** “por que não recebi o resumo da EMBJ3?” identifica confirmação vencida, ausência de
execução e ausência de entrega sem consulta administrativa.

### Etapa 3 — atividade, transcrição e memória

- listar operações recentes do próprio usuário;
- resolver fontes por nome, tipo e data;
- separar recebimento, transcrição, extração e persistência de memória;
- explicar fatos extraídos ou ausência de extração;
- implementar `explain_my_last_operation` para áudio e MacWhisper.

**Gate:** “meu último áudio entrou na memória?” responde cada estágio e sua evidência.

### Etapa 4 — produção e entrega

- correlacionar tarefa, execução de tool, mensagem de canal e outbox;
- diferenciar objetivo produzido de resultado entregue;
- explicar retries, falha terminal e próxima tentativa;
- permitir reenvio somente por uma ação explícita e idempotente.

**Gate:** o usuário consegue distinguir “foi processado” de “foi entregue”.

### Etapa 5 — integrações e efeitos externos

- adaptar Gmail, Calendar, WhatsApp e Bitrix24;
- explicar conta selecionada, conexão, rascunho, confirmação e execução;
- omitir argumentos e resultados sensíveis;
- sugerir reconexão ou nova tentativa quando permitido.

**Gate:** “meu email foi enviado ou só virou rascunho?” responde com estado persistido e conta
correta.

### Etapa 6 — capacidades e ajuda dinâmica

- implementar `get_my_capabilities`;
- gerar ajuda a partir do catálogo real de tools e permissões;
- incorporar integrações ativas e limitações da conta;
- usar a mesma fonte no onboarding e `/ajuda`.

**Gate:** ajuda, prompts e ferramentas não divergem sobre o que o usuário pode fazer.

### Etapa 7 — notificações proativas

- avisar falhas terminais e confirmações vencidas;
- alertar desconexões que bloqueiam rotinas;
- deduplicar notificações;
- respeitar preferências e limite de frequência;
- incluir causa, impacto e próximo passo em cada aviso.

**Gate:** uma falha importante não depende de o usuário perceber silenciosamente que algo não
chegou.

## Sequência recomendada de PRs

1. Contratos, estados comuns, catálogo de motivos e testes de isolamento.
2. Resolvedor e adaptador de rotinas.
3. Tools de explicação de rotina e timeline.
4. Roteamento Luna/Terra, prompts e avaliação do caso EMBJ3.
5. Atividade recente, transcrições e memória.
6. Produção, outbox e entrega.
7. Integrações e efeitos externos.
8. Capacidades dinâmicas, ajuda e onboarding.
9. Alertas proativos, preferências e métricas.

Cada PR deve ser implantável isoladamente e preservar o comportamento anterior. Não criar uma
projeção global nem migrar todos os domínios antes de validar o corte vertical de rotinas.

## Harness obrigatório para novas funcionalidades

Para cada nova capacidade, preencher e validar esta ficha:

```text
Nome da capacidade:
Recurso pertencente ao usuário:
Ação principal:
Estado consultável:
Eventos persistidos:
Estados terminais:
Códigos de motivo:
Produção separada de entrega:
Referências naturais aceitas:
Recuperações permitidas:
Confirmação necessária:
Campos sensíveis removidos:
Teste de isolamento entre usuários:
Teste de falha explicável:
Teste de sucesso comprovado:
Notificação proativa necessária:
```

Uma resposta “não aplicável” precisa de justificativa explícita no PR.

## Cenários mínimos de avaliação

### Rotina

1. Ativa e ainda não executada.
2. Executada e entregue.
3. Conteúdo produzido, entrega falhou.
4. `misfire_skipped` sem tentativa de execução.
5. Confirmação venceu.
6. Atualização pendente enquanto versão anterior segue ativa.
7. Integração necessária desconectada.
8. Tool da execução falhou após retry.

### Ingestão e memória

1. Webhook recebido e aguardando processamento.
2. Transcrição concluída, extração pendente.
3. Memória criada com evidências.
4. Transcrição válida sem fatos extraíveis.
5. Falha de provedor com retry.
6. Falha terminal.

### Efeito externo

1. Rascunho criado.
2. Aguardando confirmação.
3. Confirmação vencida.
4. Ação executada.
5. Provedor executou, entrega de resposta falhou.
6. Conta errada nunca pode ser selecionada por ambiguidade.

### Segurança

1. UUID válido de outro usuário retorna “não encontrado”.
2. Termo ambíguo pede desambiguação.
3. Segredos nunca aparecem no envelope ou na resposta.
4. Código desconhecido não gera causa inventada.
5. Consulta não altera estado.

## Métricas

- cobertura de causas conhecidas por domínio;
- percentual de diagnósticos resolvidos no primeiro turno;
- quantidade de casos que ainda exigem consulta administrativa;
- respostas com `unknown` por falta de telemetria;
- tempo entre falha, explicação e recuperação;
- operações declaradas como sucesso sem estado terminal correspondente;
- tentativas de acesso cruzado bloqueadas;
- alertas duplicados ou sem ação possível;
- divergências entre ajuda dinâmica e tools disponíveis.

## Definição de pronto

Uma funcionalidade assíncrona ou com efeito externo só está pronta quando:

- persiste estados intermediários e terminais;
- persiste um motivo seguro para falhas relevantes;
- pode ser encontrada por referência natural;
- oferece uma leitura R0 escopada ao usuário;
- separa produção de entrega;
- explica o próximo evento esperado;
- oferece recuperação compatível com a política;
- não executa recuperação durante uma consulta;
- possui testes de sucesso, falha, ambiguidade e isolamento;
- aparece no catálogo dinâmico de capacidades quando disponível.

## Não objetivos iniciais

- construir um data lake de observabilidade;
- entregar ao modelo uma tool SQL genérica;
- expor logs internos completos;
- criar um painel web paralelo antes de validar a experiência conversacional;
- unificar fisicamente todos os eventos antes do primeiro corte vertical;
- notificar todo retry transitório;
- substituir as autorizações e confirmações já existentes.

## Primeira implementação recomendada

Começar por `explain_schedule` e `list_schedule_activity`. Esse corte usa dados já persistidos,
resolve um problema real observado e valida as decisões centrais: escopo, correlação, tradução de
motivos, referência natural, separação entre produção e entrega e recuperação segura.

Depois que o caso da EMBJ3 for respondido integralmente pelo chat, o mesmo contrato deve ser
expandido para transcrições, memória, integrações e efeitos externos.
