# Arquitetura do MVP

## Objetivo

Entregar um backend simples de operar que transforme uma transcrição em memória consultável. A primeira implementação deve ser um monólito modular; separar serviços só é justificável por necessidade real de escala, segurança ou equipes independentes.

## Componentes

```text
HTTP autenticado ───────────────┐
Telegram Bot API ─────→ Supabase Edge Function
                                │ mensagem idempotente
                                ▼
                 Conversation Service ──→ histórico local
          │
 Conversation Agent ──→ Responses API
          │ function_call / function_call_output
          ▼
 Tool Registry ──→ políticas e confirmações
          │
   Application layer
     ├─ Ingestion ──→ PostgreSQL
     ├─ Memory
     └─ Retrieval
          │
 Worker ──→ extração / mensagens / outbox
```

- **API:** autentica, valida entradas e expõe comandos e consultas.
- **Application layer:** orquestra casos de uso sem conter detalhes do banco ou do provedor de IA.
- **Ingestion:** persiste a fonte antes de confirmar recebimento e cria um job idempotente.
- **Memory:** é a autoridade sobre entidades, fatos, relações e correções.
- **Retrieval:** combina filtros, busca textual e vetorial para montar uma resposta fundamentada.
- **Worker:** processa extração e indexação fora da requisição inicial.
- **Model gateway:** centraliza schemas de saída, timeout, custo, telemetria e troca de fornecedor.
- **Conversation Agent:** escolhe uma tool permitida e formula o feedback final.
- **Tool Registry:** injeta o contexto autenticado e controla schema, risco, confirmação e replay.
- **Telegram adapter:** a Edge Function hospedada verifica o segredo do webhook, normaliza texto e
  persiste a entrada antes de o worker responder.
- **Outbox:** desacopla a resposta lógica do envio pelo provedor.

## Tecnologia inicial

| Necessidade | Escolha do MVP |
| --- | --- |
| Linguagem e API | Python 3.12+, FastAPI e Pydantic. |
| API e casos de uso | Serviço único e modular executado localmente. |
| Gateway Telegram | Supabase Edge Function TypeScript/Deno publicada. |
| Banco, busca textual e vetores | PostgreSQL/Supabase com `pgvector`. |
| Processamento assíncrono | Tabela de jobs e um worker. |
| Arquivos de áudio | Não usados pelo backend. |
| IA | SDK Python oficial da OpenAI, via Responses API e gateway interno. |

O MVP usa workflows explícitos no próprio código. LangGraph ou LangChain não são dependências
iniciais: só serão introduzidos se o fluxo precisar de estado, ramificações ou observabilidade que
a orquestração simples não resolva bem. Redis, fila dedicada, grafo especializado, microsserviços e
deploy permanente da API/worker também são evoluções posteriores.

O backend envia chamadas à Responses API com `store=false`, evitando a retenção padrão de estado de aplicação da API. A documentação oficial da OpenAI descreve essa retenção padrão e o uso do SDK/Responses API: [Data controls](https://platform.openai.com/docs/models/default-usage-policies-by-endpoint), [Developer quickstart](https://platform.openai.com/docs/quickstart/make-your-first-api-request).

## Identidade, workspace e autorização

O MVP usa **Supabase Auth** como provedor de identidade, no mesmo projeto Supabase que hospeda o banco. A API valida o JWT emitido pelo provedor e obtém o `user_id` autenticado; ela nunca aceita `user_id` ou `workspace_id` como autoridade vinda do corpo da requisição.

Cada usuário recebe um workspace pessoal criado no primeiro acesso. O modelo de dados mantém `workspace_id` desde o início para não impedir futura colaboração, mas o único papel do MVP é `owner`. Todo registro de domínio contém `workspace_id` e toda consulta aplica esse filtro no repositório, sem exceção.

| Regra | Decisão do MVP |
| --- | --- |
| Identidade | JWT validado pela API usando Supabase Auth. |
| Workspace | Um workspace pessoal por usuário. |
| Papel | Apenas `owner`; colaboração fica para depois do MVP. |
| Acesso | Proprietário acessa somente registros do próprio workspace. |
| Serviço interno | Worker usa credencial de serviço e recebe `workspace_id` somente de jobs persistidos. |

Uma rota pública de health check não expõe dados. Todas as demais rotas exigem identidade autenticada.

## Fronteiras

`Capture & Ingestion` envia texto e metadados; não decide memória. A UI consome casos de uso; não implementa regras de domínio. O modelo devolve sugestões estruturadas; não recebe acesso direto ao banco ou a ações externas.

## Camada conversacional implementada

O Telegram é a interface principal do piloto. A camada conversacional independente
de canal recebe cada mensagem, identifica o usuário e permite que o agente invoque somente tools
tipadas sobre os casos de uso existentes. `POST /v1/agent/turns` oferece o mesmo fluxo para testes
sem depender do provedor. O agente não recebe uma ferramenta HTTP genérica nem controla
autenticação, `workspace_id` ou acesso ao banco.

Consultas são executadas automaticamente. Mutações exigem intenção explícita reconhecida também
pelo backend; exclusões criam uma `pending_action` e só podem ser executadas por uma confirmação
explícita em outro turno. O vínculo do Telegram exige um deep link temporário consumido pelo próprio
chat. O adaptador WhatsApp fica preservado para uma futura ativação oficial. Os detalhes estão em
[08-whatsapp-agent-tools-plan.md](08-whatsapp-agent-tools-plan.md).

## Regras de engenharia

- Fontes e eventos de auditoria são append-only.
- Projeções de busca e visões de entidade podem ser reconstruídas.
- Cada etapa assíncrona tem chave de idempotência, status, tentativas e erro normalizado.
- O serviço sempre identifica o usuário/workspace antes de ler ou gravar dados.
- Chamadas do agente usam `store=false`; histórico e resposta final ficam no PostgreSQL.
- Uma resposta final persistida permite recuperar um turno após crash sem executar o modelo de novo.
