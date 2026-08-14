# Agents & Backend — Especificação do núcleo da aplicação

## 1. Propósito deste documento

Este documento detalha a camada **Agents**, apresentada em `IDEA.md`, como um backend implementável. Essa camada é o núcleo cognitivo do produto: recebe conteúdo de diferentes canais, transforma esse conteúdo em memória persistente, recupera contexto confiável, responde perguntas, acompanha compromissos e, gradualmente, executa ações.

O termo **agent** neste projeto não significa apenas um modelo de linguagem com acesso a ferramentas. Ele representa um sistema composto por:

- orquestração de fluxos;
- modelos de IA;
- regras determinísticas;
- memória de longo prazo;
- recuperação de contexto;
- controle de autorização;
- execução idempotente de ações;
- processamento assíncrono;
- rastreabilidade e avaliação.

O backend deve continuar funcionando independentemente da interface usada. Web, aplicativo, WhatsApp, Telegram, voz ou e-mail serão clientes da mesma API e dos mesmos casos de uso.

---

## 2. Resultado que a camada deve atingir

A camada Agents deve ser capaz de transformar informação dispersa em conhecimento útil e acionável.

Seu ciclo principal é:

```text
capturar -> interpretar -> registrar evidências -> consolidar memória
         -> recuperar contexto -> raciocinar -> recomendar ou agir
```

Na prática, o backend deve conseguir:

1. receber uma transcrição ou outro conteúdo sem processá-lo duas vezes;
2. preservar o conteúdo original como evidência;
3. identificar entidades, eventos, decisões, fatos, compromissos e oportunidades;
4. relacionar novas informações à memória existente;
5. representar mudanças ao longo do tempo sem apagar o histórico;
6. responder usando as informações mais relevantes, atuais e confiáveis;
7. mostrar de onde veio cada afirmação importante;
8. reconhecer incerteza, conflito e ausência de evidência;
9. sugerir próximos passos úteis;
10. executar ações somente dentro do nível de autonomia autorizado;
11. gerar briefings e alertas proativos sem se tornar invasivo;
12. permitir ao usuário revisar, corrigir e excluir sua memória.

### Métrica de valor central

O MVP deve validar a seguinte hipótese:

> Depois de acumular gravações reais, o usuário consegue recuperar decisões, compromissos, contexto e mudanças importantes com menos esforço e mais confiança do que consultando manualmente as fontes originais.

Quantidade de transcrições ou de chamadas ao modelo são métricas operacionais, não o resultado do produto. O valor deve ser medido por recuperação correta, cobertura das informações importantes, confiança nas fontes e utilidade percebida.

---

## 3. Escopo e limites

### 3.1. Responsabilidades de Agents

- receber Transcript Events normalizados de Capture & Ingestion, além de texto de UI e futuras integrações;
- coordenar pipelines síncronos e assíncronos;
- armazenar fontes e evidências;
- extrair e normalizar conhecimento;
- resolver entidades e relações;
- consolidar memória sem perder versões anteriores;
- indexar memória para diferentes estratégias de busca;
- responder perguntas com proveniência;
- gerenciar conversas e contexto de curta duração;
- manter pendências e estados operacionais;
- registrar e executar Skills;
- aplicar políticas de risco, autorização e autonomia;
- agendar avaliações proativas;
- expor uma API estável para qualquer interface;
- produzir auditoria, métricas, traces e dados para avaliação.

### 3.2. Fora do escopo de Agents

- capturar áudio no dispositivo;
- controlar microfone ou interface de gravação;
- receber ou armazenar áudio no fluxo de voz do MVP;
- executar a transcrição do áudio capturado pelo iPhone;
- implementar telas e apresentação visual;
- conter regras específicas de WhatsApp, Telegram ou Web;
- tratar a saída de um modelo como fonte de verdade;
- permitir que o modelo grave livremente no banco de dados;
- enviar ações de alto risco sem autorização;
- substituir o conteúdo original pela memória derivada.

### 3.3. Escopo recomendado para o MVP

O MVP deve priorizar um único fluxo vertical bem resolvido:

```text
transcrição recebida
  -> conhecimento extraído
  -> memória consolidada
  -> pergunta respondida com fontes
  -> correção possível pelo usuário
```

Não é necessário, nessa fase, implementar múltiplos agentes autônomos, grafo especializado, dezenas de Skills ou automações irreversíveis. As fronteiras lógicas devem existir desde o início, mesmo que os módulos sejam implantados no mesmo serviço.

### 3.4. Arquitetura alvo versus implementação do MVP

Este documento descreve tanto o destino arquitetural quanto a primeira implementação. Eles não devem ser confundidos:

| Área | MVP | Arquitetura alvo |
| --- | --- | --- |
| Entrada de voz | transcrição + metadados do iPhone | Transcript Events de múltiplos dispositivos e canais |
| Persistência | PostgreSQL/Supabase + `pgvector` | componentes separados apenas quando escala ou isolamento exigirem |
| Processamento assíncrono | tabela de jobs + worker simples | fila/event bus dedicado e múltiplos workers se necessário |
| Memória | 7 entidades essenciais | domínio conceitual completo e novas projeções |
| Retrieval | filtros SQL + full-text + vector + reranking simples | planos dinâmicos e travessias de relações quando justificadas |
| Agent runtime | workflows especializados | loops agentic limitados para objetivos compostos |
| Skills | fora do core do MVP ou somente leitura/rascunho | ações externas com autonomia progressiva |
| Proatividade | somente após gates de qualidade | briefings e alertas contextuais configuráveis |

A arquitetura alvo orienta contratos e limites. Ela não autoriza implementar componentes futuros antes de serem necessários para validar a hipótese do MVP.

---

## 4. Princípios de engenharia

### 4.1. Memory first

A memória é um ativo durável. Modelos, prompts, índices e interfaces podem ser substituídos. Fontes, evidências, fatos versionados e correções não podem depender de um fornecedor de IA.

### 4.2. Fonte antes da inferência

Toda memória derivada deve apontar para evidências. Uma resposta deve diferenciar claramente:

- o que foi explicitamente registrado;
- o que foi inferido pelo sistema;
- o que é uma sugestão;
- o que não se sabe.

### 4.3. Histórico imutável, projeções mutáveis

Fontes e eventos de auditoria são append-only. A visão atual de uma entidade ou Wiki pode ser reconstruída e atualizada. Uma mudança nunca deve apagar silenciosamente a verdade anterior.

### 4.4. IA propõe; domínio valida

O modelo pode classificar, extrair e sugerir operações. Serviços de domínio devem validar schema, permissões, invariantes, transições de estado e idempotência antes de persistir ou agir.

### 4.5. Workflows antes de autonomia aberta

Os primeiros casos de uso devem ser fluxos limitados, observáveis e reproduzíveis. Um loop agentic aberto só deve ser usado onde planejamento dinâmico realmente gere valor, sempre com limites de passos, custo, tempo e ferramentas.

### 4.6. Segurança por padrão

Uma transcrição pode conter dados pessoais, empresariais e confidenciais. Isolamento por usuário/workspace, criptografia, retenção, auditoria e direito de exclusão são requisitos centrais, não melhorias futuras.

### 4.7. Assíncrono e idempotente

Ingestão do Transcript Event, extração, indexação e briefings podem falhar ou repetir. Todo estágio deve aceitar retry sem duplicar memórias ou ações. A transcrição ocorre antes da fronteira de Agents.

### 4.8. Interface independente

Casos de uso retornam objetos de domínio e eventos, nunca componentes ou textos formatados especificamente para um canal.

---

## 5. Arquitetura lógica

```text
iPhone / Omi / Plaud / WhatsApp / Desktop / Meeting Bot
                 |
                 | Transcript Event: texto + metadados
                 v
         API + Autenticação
                 |
       +---------+----------+
       |                    |
       v                    v
 Command/Query API      Transcript Ingestion API
       |                    |
       +---------+----------+
                 v
         Application Layer
       (casos de uso/orquestração)
                 |
    +------------+-------------+----------------+
    |            |             |                |
    v            v             v                v
Knowledge     Memory Core   Retrieval       Action Engine
Ingestion                    & Answering
    |            |             |                |
    +------------+-------------+----------------+
                 |
       Scheduler / Proactivity
                 |
        +--------+---------+
        |                  |
        v                  v
 PostgreSQL + pgvector  Worker / Jobs
        |
        v
 Model Gateway + Skill Adapters
```

### 5.1. Estratégia de implantação inicial

Recomenda-se começar com um **monólito modular**, PostgreSQL/Supabase, `pgvector` e um worker simples. Uma tabela de jobs pode cumprir inicialmente o papel de fila. Isso reduz o custo operacional sem misturar responsabilidades. Cada módulo deve ter contratos claros e não acessar tabelas internas de outro módulo de forma indiscriminada.

Redis, SQS, Kafka, object storage e serviços de busca separados só devem ser introduzidos quando uma necessidade concreta justificar a operação adicional. Separar em microsserviços também depende de razões mensuráveis, como escala diferente, isolamento de segurança, equipes independentes ou ciclos de implantação conflitantes.

### 5.2. Componentes

#### API e autenticação

Responsável por autenticar, identificar usuário/workspace, validar contratos, aplicar rate limit e transformar chamadas externas em comandos ou consultas internas.

#### Application Layer / Orchestrator

Coordena casos de uso. Não contém detalhes de modelo, banco, canal ou fornecedor externo. Exemplos: `IngestContent`, `AskMemory`, `CorrectMemory`, `ApproveAction` e `GenerateMeetingBriefing`.

#### Knowledge Ingestion

Normaliza transcrições e outras fontes textuais, fragmenta conteúdo, extrai candidatos a conhecimento e dispara consolidação. Deve produzir saídas estruturadas e versionadas. Não recebe áudio nem executa speech-to-text.

#### Memory Core

É a autoridade sobre entidades, fatos, relações, eventos, compromissos, versões, conflitos e proveniência. Aplica as regras temporais e de resolução de entidades.

#### Retrieval & Answering

Interpreta a intenção, cria um plano de busca, combina resultados estruturados e não estruturados, monta contexto, gera resposta e valida citações.

#### Action Engine

Mantém o catálogo de Skills, políticas de risco, pedidos de aprovação, execuções, retries e recibos de sistemas externos.

#### Scheduler / Proactivity

Avalia sinais temporais ou eventos, identifica possíveis intervenções, calcula relevância, respeita preferências e entrega uma sugestão ou ação aprovada ao adaptador de canal.

#### Model Gateway

Abstrai provedores e modelos. Centraliza schema de saída, timeouts, retries, limites, custo, versionamento de prompts, políticas de dados e telemetria. Nenhum módulo de domínio deve depender diretamente do SDK de um provedor.

#### Workers e fila

Executam tarefas longas ou recuperáveis. Cada job tem identificador, status, tentativas, erro normalizado e chave de idempotência. No MVP, uma tabela de jobs e um worker são suficientes; uma fila dedicada é uma evolução operacional, não uma pré-condição.

---

## 6. Um agente, vários papéis lógicos

No início, não há ganho obrigatório em criar uma “sociedade” de agentes. Recomenda-se um orquestrador com papéis especializados e contratos estruturados:

| Papel lógico | Entrada | Saída | Pode persistir diretamente? |
| --- | --- | --- | --- |
| Extrator | trecho + contexto | candidatos tipados | não |
| Resolvedor de entidades | candidato + entidades próximas | match, novo ou revisão | não |
| Memory Manager | candidatos + memória atual | operações propostas | não |
| Validador de memória | operações + evidências | operações válidas/rejeitadas | sim, via serviço de domínio |
| Planejador de retrieval | pergunta + contexto | plano de busca limitado | não |
| Sintetizador | pergunta + evidências | resposta com referências | não |
| Planejador de ação | objetivo + Skills permitidas | plano limitado | não |
| Executor | ação já validada/aprovada | resultado e recibo | registra execução |
| Agente proativo | sinais + preferências | intervenção candidata | não |

Esses papéis podem usar chamadas de modelo diferentes, mas devem compartilhar identidade, autorização, observabilidade e limites. Se futuramente forem processos independentes, os contratos já estarão definidos.

---

## 7. Modelo de memória

### 7.1. Camadas

#### Raw Memory

Conteúdo original recebido por Agents, preservado para auditoria e reprocessamento. No fluxo de voz do MVP, Raw Memory contém:

- transcrição original e versão normalizada;
- `capture_id` único;
- origem e data da captura;
- idioma e duração informados pelo dispositivo;
- timestamps ou participantes, quando fornecidos;
- demais metadados do Transcript Event.

O áudio não pertence à Raw Memory de Agents, não cruza essa fronteira e não é armazenado no backend. Futuras fontes textuais, como documento, e-mail ou mensagem, poderão usar o mesmo princípio de preservar o conteúdo recebido e seus metadados.

#### Semantic Memory

Conhecimento declarativo derivado:

- pessoas, empresas, projetos e locais;
- fatos e relações;
- preferências;
- conceitos e conhecimento institucional.

#### Episodic Memory

Eventos situados no tempo:

- reunião;
- ligação;
- conversa;
- decisão tomada;
- mudança de contexto;
- interação com cliente.

Essa camada deve ser explícita, pois responde “o que aconteceu, quando e com quem”.

#### Operational Memory

Itens que possuem ciclo de vida:

- compromisso;
- pendência;
- tarefa;
- prazo;
- promessa;
- problema;
- oportunidade;
- próxima ação.

#### Interaction Memory

Histórico recente de conversa necessário para dar continuidade ao diálogo. Não deve ser promovido automaticamente a memória de longo prazo. Promoção exige relevância, evidência e uma operação de memória válida.

### 7.2. Entidades principais

O modelo conceitual completo contém as entidades abaixo, mas elas não precisam se tornar uma tabela independente no MVP:

| Entidade | Função |
| --- | --- |
| `Workspace` | fronteira de isolamento e propriedade dos dados |
| `User` | identidade, preferências e políticas de autonomia |
| `Source` | unidade textual original recebida, como uma transcrição ou documento |
| `ContentSegment` | trecho endereçável de uma fonte |
| `Entity` | pessoa, empresa, projeto, produto, local ou conceito |
| `EntityAlias` | nome alternativo usado na resolução |
| `Mention` | ocorrência de uma entidade em um trecho |
| `Fact` | afirmação versionada sobre entidades |
| `Relation` | ligação tipada entre entidades |
| `Episode` | evento ocorrido no tempo |
| `Decision` | decisão, seu contexto e participantes |
| `ActionItem` | compromisso, tarefa ou pendência com status |
| `Evidence` | vínculo exato entre memória derivada e fonte |
| `MemoryCorrection` | correção explícita feita pelo usuário |
| `Conversation` | sessão de interação de curta duração |
| `SkillDefinition` | contrato e política de uma capacidade externa |
| `ActionRequest` | proposta de execução e seu risco |
| `ActionExecution` | tentativa, resultado e recibo da ação |
| `ProactiveCandidate` | possível alerta ou briefing antes da entrega |

#### Subconjunto persistido no MVP

A primeira implementação deve priorizar sete entidades:

| Entidade | Responsabilidade no MVP |
| --- | --- |
| `Source` | transcrição original, metadados, `capture_id` e estado de processamento |
| `Entity` | pessoa, empresa, projeto ou outro objeto identificado; aliases podem ser dados associados |
| `Fact` | afirmação versionada; decisões e relações simples podem ser tipos de fato |
| `Episode` | reunião, conversa ou acontecimento temporal; decisões contextuais podem ser tipos de episódio |
| `ActionItem` | compromisso, tarefa ou pendência com ciclo de vida |
| `Evidence` | vínculo preciso entre item derivado e trecho da fonte |
| `Correction` | alteração confirmada pelo usuário e sua precedência sobre inferências |

`Workspace` e `User` continuam sendo estruturas transversais de identidade e isolamento. `ContentSegment`, `EntityAlias`, `Mention`, `Relation`, `Decision`, `Conversation`, objetos de Skills e proatividade podem começar como campos, tipos ou projeções e ganhar persistência própria quando casos de uso reais exigirem.

O objetivo dessa simplificação é reduzir acoplamento inicial, não remover conceitos da arquitetura alvo.

### 7.3. Estrutura mínima de um fato temporal

```json
{
  "id": "fact_01",
  "workspace_id": "ws_01",
  "subject_entity_id": "project_x",
  "predicate": "delivery_deadline",
  "value": { "type": "date", "value": "2026-11-30" },
  "status": "current",
  "valid_from": "2026-08-17T14:00:00Z",
  "valid_to": null,
  "observed_at": "2026-08-17T14:00:00Z",
  "recorded_at": "2026-08-17T17:12:00Z",
  "extraction_confidence": 0.98,
  "source_reliability": "unknown",
  "memory_confidence": 0.87,
  "supersedes_fact_id": "fact_previous_deadline",
  "derivation": "explicit_statement",
  "evidence_ids": ["evidence_52"],
  "created_by": "ingestion_pipeline:v3"
}
```

Datas diferentes não podem ser colapsadas:

- `observed_at`: quando a informação foi expressa ou observada;
- `recorded_at`: quando entrou no sistema;
- `valid_from` / `valid_to`: período em que o fato é considerado válido no mundo real.

Confianças também não devem ser colapsadas em um único número:

- `extraction_confidence`: confiança de que o conteúdo da fonte foi interpretado corretamente;
- `source_reliability`: confiabilidade conhecida da origem ou de quem fez a afirmação;
- `memory_confidence`: confiança consolidada de que o fato representa a memória atual.

No MVP, `source_reliability` pode começar como `unknown` e `memory_confidence` pode usar uma regra simples. A separação no contrato evita confundir uma extração clara com uma afirmação verdadeira. Por exemplo, o modelo pode extrair com alta confiança “talvez o prazo seja novembro”, mas a modalidade especulativa deve reduzir a confiança da memória.

### 7.4. Estados recomendados

Um fato pode estar em `candidate`, `current`, `superseded`, `disputed`, `rejected` ou `deleted`.

Um item operacional pode estar em `open`, `in_progress`, `blocked`, `done`, `cancelled` ou `unknown`.

Uma correção explícita do usuário tem precedência sobre inferências automáticas, mas também deve manter histórico e autoria.

### 7.5. Evidência e proveniência

Uma evidência deve conter:

- `source_id`;
- localizador tipado dentro da fonte;
- trecho exato necessário para inspeção;
- método e versão que criaram a memória;
- data de extração;
- confiança de extração;
- indicação de afirmação explícita ou inferida.

Exemplo baseado na transcrição:

```json
{
  "source_id": "capture_123",
  "locator": {
    "type": "text_span",
    "start_char": 428,
    "end_char": 517
  },
  "quote": "Decidimos alterar o prazo para novembro."
}
```

O localizador é independente da origem. `text_span` é o padrão do MVP. Se uma fonte futura fornecer timestamps confiáveis, também poderá usar `time_span` com `start_ms` e `end_ms`. Um localizador nunca deve pressupor que o backend possui o áudio.

Uma resposta que afirma algo factual deve conseguir retornar `source_id` e o trecho correspondente. A referência nunca deve ser inventada pelo sintetizador.

### 7.6. Banco e índices

Para o MVP:

- PostgreSQL ou Supabase como fonte de verdade para transcrições, entidades, fatos, estados, jobs e auditoria;
- full-text search do próprio PostgreSQL para nomes, aliases e termos exatos;
- `pgvector` para recuperação semântica de trechos e memórias;
- relações simples representadas no modelo relacional ou como fatos tipados.

Não há necessidade de object storage no fluxo de voz do MVP, pois Agents não recebe áudio. Ele só deverá ser adicionado futuramente se uma nova fonte exigir persistência de arquivos não textuais.

Um banco de grafos pode ser avaliado depois. O domínio é um grafo conceitual, mas isso não obriga o MVP a adotar uma tecnologia de grafo. A necessidade deve ser demonstrada por consultas e escala reais.

Índices vetoriais e projeções da Wiki são derivados reconstruíveis, não a fonte de verdade.

### 7.7. Wiki como projeção reconstruível

A Wiki nunca deve ser uma grande página Markdown editada diretamente pelo modelo ou pelo Memory Manager. Ela é uma visão derivada do estado consolidado:

```text
Sources
   ↓
Evidence
   ↓
Facts / Episodes / ActionItems
   ↓
Current Memory State
   ↓
Wiki Projection
```

Uma correção, nova evidência ou alteração na regra de consolidação deve permitir reconstruir a página de uma pessoa, empresa ou projeto. A projeção pode ser armazenada em cache por desempenho, mas não recebe mudanças que não existam primeiro na memória estruturada e rastreável.

#### Estrutura semiestruturada e adaptativa

A Wiki não deve possuir uma taxonomia rígida ou uma lista fechada de seções. O sistema deve fornecer ao Wiki Agent uma estrutura idealizada para garantir uma experiência minimamente consistente, mas essa estrutura funciona como orientação, não como schema obrigatório da página.

Uma página de projeto, por exemplo, pode começar com:

```text
Projeto
├── Resumo
├── Estado atual
├── Pessoas relacionadas
├── Decisões
├── Pendências
├── Últimas interações
└── Timeline
```

Se a memória disponível justificar, o Wiki Agent poderá criar novos subtópicos como:

```text
├── Riscos regulatórios
├── Restrições técnicas
├── Hipóteses em validação
├── Concorrentes mencionados
├── Estratégia de negociação
└── Unidades de expansão
```

Essa liberdade também se aplica a pessoas, empresas, produtos, assuntos e futuras entidades. Duas páginas do mesmo tipo não precisam ter exatamente as mesmas seções quando seus contextos forem diferentes.

O Wiki Agent poderá:

- adicionar um subtópico quando houver conhecimento relevante que não se encaixe bem na estrutura existente;
- omitir seções vazias ou sem utilidade;
- agrupar itens relacionados para melhorar compreensão;
- dividir uma seção que tenha se tornado ampla ou ambígua;
- renomear subtópicos para refletir melhor o vocabulário do usuário;
- sugerir uma nova categoria reutilizável quando um padrão surgir em várias entidades;
- reorganizar a apresentação conforme a memória evoluir.

Essa liberdade é limitada pelas seguintes invariantes:

- todo conteúdo factual exibido deve derivar de `Fact`, `Episode`, `ActionItem` ou outra memória estruturada com `Evidence`;
- o agente não pode criar um fato apenas para preencher uma seção da Wiki;
- inferências e sugestões devem ser identificadas como tais;
- a criação de um subtópico não cria automaticamente um novo tipo no schema da memória;
- títulos e organização podem ser flexíveis, mas identidade, temporalidade, status e proveniência continuam estruturados;
- reorganizar a Wiki não altera nem exclui a memória subjacente;
- qualquer projeção deve poder ser reconstruída a partir da memória e da versão das regras do Wiki Agent.

A separação conceitual é:

```text
Modelo de memória
= estruturado, versionado e governado por invariantes

Estrutura da Wiki
= adaptativa, contextual e organizada pelo Wiki Agent
```

#### Contrato conceitual da projeção

Em vez de um schema que enumera todas as seções possíveis, a projeção deve aceitar uma árvore de seções tipadas de forma genérica:

```json
{
  "entity_id": "project_x",
  "title": "Projeto X",
  "projection_version": 4,
  "template": "project_default",
  "sections": [
    {
      "section_id": "current_state",
      "title": "Estado atual",
      "section_type": "standard",
      "items": [
        {
          "memory_ref": "fact_32",
          "evidence_ids": ["evidence_52"]
        }
      ]
    },
    {
      "section_id": "regulatory_risks",
      "title": "Riscos regulatórios",
      "section_type": "agent_defined",
      "rationale": "Há três memórias relacionadas a exigências regulatórias.",
      "items": [
        {
          "memory_ref": "fact_81",
          "evidence_ids": ["evidence_94"]
        }
      ]
    }
  ]
}
```

`template` fornece apenas a estrutura inicial idealizada. `section_type = agent_defined` permite novos subtópicos sem migração de banco ou mudança do contrato. Os itens devem referenciar memórias existentes; texto de apresentação pode ser regenerado.

#### Ciclo do Wiki Agent

```text
memória de uma entidade mudou
        ↓
carregar projeção atual e estrutura idealizada
        ↓
identificar assuntos relevantes e recorrentes
        ↓
manter, criar, dividir, agrupar ou remover seções vazias
        ↓
validar referências e evidências
        ↓
publicar nova versão reconstruível da projeção
```

O resultado esperado é uma Wiki que desenvolve uma organização adequada à realidade do usuário, sem transformar flexibilidade editorial em liberdade para inventar conhecimento.

---

## 8. Pipeline de ingestão

### 8.1. Contrato de entrada

```json
{
  "capture_id": "capture_123",
  "source": "iphone",
  "captured_at": "2026-08-13T10:35:00-03:00",
  "duration_seconds": 1840,
  "language": "pt-BR",
  "transcript": "Conversei hoje com Carlos sobre o Projeto Alfa...",
  "metadata": {
    "transcription_engine": "on_device",
    "transcript_version": 1
  }
}
```

O workspace é obtido da identidade autenticada, não do payload. `capture_id` é a chave de idempotência da captura e deve ser reutilizado em todos os retries da mesma transcrição.

O backend deve primeiro persistir a transcrição de forma durável e então responder rapidamente com `source_id`, `job_id` e indicação de duplicata. O processamento completo ocorre de forma assíncrona. Essa confirmação autoriza o iPhone a excluir o áudio local; portanto, ela não poderá ser emitida antes do commit da Raw Memory.

O contrato não aceita conteúdo binário, URL de mídia ou localização de áudio no fluxo normal.

### 8.2. Estágios

```text
1. aceitar e autenticar
2. validar contrato, `capture_id` e idempotência
3. preservar a transcrição e confirmar recebimento durável
4. normalizar idioma, participantes e datas
5. executar Validation Gate semântico
6. segmentar mantendo offsets de caracteres e timestamps fornecidos, quando existirem
7. classificar relevância e tipo de conteúdo
8. extrair candidatos estruturados
9. resolver entidades
10. validar evidências e schemas
11. comparar com a memória atual
12. criar, confirmar, disputar ou substituir memórias
13. atualizar índices e projeções
14. publicar eventos de conclusão
```

O Validation Gate de Agents não repete a validação básica feita no iPhone. Ele decide se o texto contém material processável, distingue conteúdo sem valor semântico de uma memória curta válida e registra o motivo quando não houver conhecimento a consolidar. A transcrição original permanece disponível mesmo quando nenhum fato for extraído.

### 8.3. Saída estruturada do extrator

O extrator não deve gerar SQL nem texto livre a ser persistido diretamente. Deve produzir candidatos tipados, por exemplo:

```json
{
  "entities": [
    {
      "temporary_id": "person_1",
      "type": "person",
      "canonical_name": "Carlos",
      "aliases": [],
      "evidence_ref": "capture_123#char=12,18"
    }
  ],
  "facts": [
    {
      "type": "decision",
      "statement": "Entregar a primeira fase até setembro de 2026",
      "project_ref": "project_1",
      "decided_at": "2026-08-13",
      "modality": "asserted",
      "extraction_confidence": 0.91,
      "evidence_ref": "capture_123#char=30,81"
    }
  ],
  "action_items": [
    {
      "description": "Enviar a documentação técnica",
      "owner_ref": "person_1",
      "status": "open",
      "due_at": null,
      "extraction_confidence": 0.88,
      "evidence_ref": "capture_123#char=82,120"
    }
  ]
}
```

Schemas devem rejeitar tipos desconhecidos, datas impossíveis, referências inexistentes e candidatos sem evidência.

### 8.4. Idempotência e reprocessamento

- o mesmo `capture_id` retorna a fonte e o job originais;
- cada estágio registra versão e checksum de entrada;
- retry não cria uma segunda fonte nem duplica fatos;
- uma correção ou versão explicitamente nova da transcrição mantém o vínculo com a captura e cria uma execução de processamento distinta; retry não cria versão;
- reprocessar não apaga correções do usuário;
- operações de memória devem possuir uma chave natural ou fingerprint;
- índices só são atualizados depois do commit da memória;
- publicação de eventos deve usar um padrão transacional, como outbox.

### 8.5. Falhas

Cada estágio deve terminar em `succeeded`, `retryable_failure`, `permanent_failure` ou `needs_review`. Uma fonte parcialmente processada não pode aparecer como totalmente consolidada. O usuário deve conseguir consultar o estado e solicitar nova tentativa.

---

## 9. Entity Resolution

### 9.1. Objetivo

Determinar se “Carlos”, “Carlos Silva” e “Carlos da Empresa Alfa” são a mesma pessoa, sem fundir pessoas diferentes por engano.

### 9.2. Processo

1. normalizar nome e aliases;
2. buscar candidatos no mesmo workspace;
3. comparar atributos fortes, relações e contexto;
4. calcular score e motivos;
5. decidir `match`, `create` ou `needs_review`;
6. registrar a decisão de resolução para auditoria.

Sinais úteis incluem e-mail, telefone, empresa, cargo, projeto, participantes recorrentes e proximidade temporal. Similaridade de nome isolada é um sinal fraco.

### 9.3. Política de confiança

- alta confiança: vínculo automático;
- confiança intermediária: manter candidato e pedir confirmação quando relevante;
- baixa confiança: criar entidade separada ou deixar referência não resolvida;
- atributos conflitantes: não fundir automaticamente.

Merge e split de entidades devem ser reversíveis. IDs antigos precisam continuar resolvendo para preservar referências.

---

## 10. Memory Manager

### 10.1. Decisões possíveis

Para cada candidato, o Memory Manager propõe uma destas operações:

- `CREATE`: conhecimento ainda inexistente;
- `CONFIRM`: nova evidência reforça o mesmo conhecimento;
- `SUPERSEDE`: uma nova verdade substitui a anterior a partir de uma data;
- `DISPUTE`: fontes confiáveis divergem e não há resolução segura;
- `ENRICH`: complementa sem alterar o significado;
- `IGNORE_DUPLICATE`: repetição sem evidência adicional útil;
- `REQUEST_REVIEW`: ambiguidade com impacto relevante.

O MVP deve implementar e avaliar primeiro `CREATE`, `CONFIRM`, `SUPERSEDE` e `DISPUTE`. As demais operações permanecem no contrato da arquitetura alvo, mas não precisam de heurísticas sofisticadas na primeira versão.

Fluxo mínimo:

```text
novo fato
   ↓
já existe?
   ├── não → CREATE
   └── sim
        ↓
é equivalente?
   ├── sim → CONFIRM
   └── não
        ↓
há relação temporal clara?
   ├── sim → SUPERSEDE
   └── não → DISPUTE
```

### 10.2. Regras essenciais

- similaridade textual não basta para declarar duplicata;
- uma nova data pode representar atualização, não contradição;
- fatos históricos continuam válidos para perguntas sobre o passado;
- conflito não deve ser resolvido pela “opinião” do modelo;
- confiança de extração, confiabilidade da fonte e confiança consolidada da memória são dimensões distintas;
- múltiplas evidências podem apoiar o mesmo fato;
- correções do usuário não são sobrescritas por reprocessamento automático;
- memórias de alto impacto e baixa confiança entram em revisão.

O modelo pode propor uma operação como:

```json
{
  "operation": "SUPERSEDE",
  "previous_fact_id": "fact_17"
}
```

Antes da persistência, uma regra determinística deve verificar pelo menos workspace, sujeito, predicado, compatibilidade dos valores, evidências e intervalos temporais. Sem uma relação temporal válida, o resultado deve ser `DISPUTE` ou revisão, nunca uma substituição decidida apenas pelo LLM.

### 10.3. Exemplo temporal

```text
03/08: Projeto X -> prazo setembro (superseded, válido até 16/08)
17/08: Projeto X -> prazo novembro  (current, válido desde 17/08)
```

Para “qual é o prazo?”, a resposta usa novembro. Para “qual era o prazo em 10/08?”, usa setembro. Para “o prazo mudou?”, retorna ambos, a transição e suas fontes.

---

## 11. Retrieval e respostas

### 11.1. Pipeline de consulta

```text
pergunta
  -> autenticação e escopo
  -> classificação de intenção
  -> entidades e período mencionados
  -> plano de recuperação
  -> buscas em paralelo
  -> ranking e deduplicação
  -> montagem do contexto
  -> síntese fundamentada
  -> validação de citações e política
  -> resposta
```

### 11.2. Recuperação híbrida

No MVP, a recuperação deve seguir uma sequência simples:

1. filtros estruturados no PostgreSQL;
2. full-text search;
3. vector search com `pgvector`;
4. reranking simples por tipo, confiança, validade e recência.

A arquitetura alvo poderá adicionar:

- travessia limitada de relações;
- planos dinâmicos de recuperação;
- episódios relacionados;
- preferências e escopo do usuário.

Exemplo para “O que ficou pendente com Carlos?”:

```text
entity = pessoa Carlos resolvida
memory_type = action_item
status in (open, in_progress, blocked, unknown)
related_episode.participant = Carlos
order by due_date, importance, recency
```

Busca vetorial pura não atende corretamente esse caso.

### 11.3. Montagem de contexto

O contexto entregue ao modelo deve conter objetos compactos, fontes e relações relevantes, não uma concatenação indiscriminada de transcrições. Deve haver limites de quantidade, tokens e diversidade de fontes.

O sistema deve evitar:

- favorecer apenas trechos recentes sem respeitar validade;
- apresentar uma memória substituída como atual;
- duplicar a mesma evidência em vários resultados;
- misturar dados de workspaces;
- tratar conteúdo recuperado como instrução de sistema.

### 11.4. Contrato de resposta

```json
{
  "answer": "A última posição registrada é ...",
  "claims": [
    {
      "text": "O prazo atual é novembro de 2026.",
      "evidence_ids": ["evidence_52"],
      "confidence": "high"
    }
  ],
  "sources": [
    {
      "source_id": "capture_456",
      "title": "Reunião de 17/08/2026",
      "locator": {
        "type": "text_span",
        "start_char": 428,
        "end_char": 517
      },
      "quote": "Decidimos alterar o prazo para novembro."
    }
  ],
  "uncertainties": [],
  "suggested_actions": []
}
```

### 11.5. Regras de resposta

- afirmar apenas o que estiver suportado pelas evidências recuperadas;
- informar quando não houver dados suficientes;
- apresentar conflito quando ele existir;
- priorizar a versão atual em perguntas no presente;
- respeitar o intervalo temporal da pergunta;
- citar fontes próximas às afirmações importantes;
- não transformar sugestão em decisão registrada;
- não executar ação implícita em uma pergunta.

---

## 12. Agent runtime e uso de ferramentas

### 12.1. Quando usar um loop agentic

Um loop de planejamento é útil para solicitações compostas, como:

> Prepare um briefing da reunião de amanhã, confira pendências dos participantes e produza um rascunho de follow-up.

Consultas simples devem usar workflows diretos. Isso melhora latência, custo e previsibilidade.

### 12.2. Estado mínimo de uma execução

```json
{
  "run_id": "run_01",
  "workspace_id": "ws_01",
  "actor_user_id": "user_01",
  "goal": "...",
  "allowed_skills": ["search_memory", "get_calendar", "draft_email"],
  "max_steps": 8,
  "deadline_at": "...",
  "budget": { "model_tokens": 30000 },
  "status": "running",
  "steps": []
}
```

### 12.3. Loop controlado

1. validar objetivo e permissões;
2. selecionar somente Skills permitidas;
3. produzir próximo passo estruturado;
4. validar argumentos;
5. executar leitura ou solicitar aprovação de escrita;
6. anexar resultado e recibo ao estado;
7. interromper ao atingir objetivo, limite, erro ou necessidade de usuário;
8. gerar resposta final com rastreabilidade.

O modelo nunca recebe credenciais e nunca decide sozinho que possui uma permissão.

---

## 13. Skills e ações externas

### 13.1. Contrato de Skill

Cada Skill deve declarar:

- nome e versão;
- descrição sem ambiguidade;
- schema de entrada e saída;
- permissões necessárias;
- classificação de risco;
- se produz efeito externo;
- se suporta dry-run;
- estratégia de idempotência;
- timeout e política de retry;
- dados sensíveis manipulados;
- adaptador responsável.

Exemplo:

```json
{
  "name": "send_email",
  "version": "1.0",
  "risk": "high",
  "side_effect": true,
  "requires_approval": true,
  "supports_dry_run": true,
  "required_scopes": ["email.send"]
}
```

### 13.2. Níveis de autonomia

| Nível | Comportamento | Exemplos iniciais |
| --- | --- | --- |
| leitura | executa automaticamente | buscar memória, consultar agenda |
| sugestão | gera proposta sem efeito externo | sugerir lembrete, recomendar follow-up |
| rascunho | prepara resultado para revisão | redigir e-mail ou mensagem |
| aprovação | só executa após confirmação explícita | criar evento, enviar e-mail |
| automático | executa por regra previamente concedida | apenas ações específicas e reversíveis |

Risco é contextual. Criar um evento privado pode ser médio risco; convidar terceiros já produz efeito externo maior.

### 13.3. Fluxo de aprovação

```text
planejar ação
 -> validar permissão e risco
 -> gerar preview imutável
 -> registrar ActionRequest
 -> solicitar aprovação
 -> usuário aprova a versão exata
 -> revalidar expiração e contexto
 -> executar com chave de idempotência
 -> armazenar recibo
 -> informar resultado
```

Se destinatário, conteúdo ou horário mudar após a aprovação, uma nova aprovação é necessária.

### 13.4. Garantias

- timeout não significa automaticamente que a ação falhou; consultar o provedor antes de repetir;
- retries usam a mesma chave de idempotência;
- toda execução gera log auditável;
- tokens de integrações ficam em cofre de segredos;
- revogar uma integração impede novas execuções;
- ações destrutivas devem ser reversíveis quando o provedor permitir;
- o usuário visualiza o que aconteceu e qual regra autorizou.

---

## 14. Scheduler e proatividade

Proatividade só deve ser habilitada depois que a memória, o retrieval e as respostas atingirem níveis mínimos de qualidade definidos na seção de avaliação:

```text
Memory Quality
     ↓
Retrieval Quality
     ↓
Answer Quality
     ↓
Proactivity
     ↓
Actions
```

Essa dependência é um gate de produto. Se pessoas, datas ou pendências ainda forem pouco confiáveis, a proatividade amplificará o erro ao interromper o usuário com informação incorreta. Manter o módulo na arquitetura não significa desenvolvê-lo antes desses gates.

### 14.1. Proatividade como pipeline

```text
sinal temporal ou evento
 -> gerar candidato
 -> recuperar contexto
 -> avaliar relevância e novidade
 -> aplicar preferências e janela de silêncio
 -> deduplicar
 -> gerar conteúdo
 -> entregar ao adaptador de canal
 -> medir feedback
```

### 14.2. Tipos de sinal

- reunião futura;
- prazo se aproximando;
- compromisso vencido;
- promessa sem atualização;
- cliente sem contato por período relevante;
- nova informação que contradiz memória importante;
- padrão recorrente identificado em várias fontes.

### 14.3. Controles contra ruído

- limite diário e por categoria;
- horários permitidos e fuso do usuário;
- cooldown por entidade/assunto;
- score mínimo de confiança e utilidade;
- deduplicação de alertas;
- não alertar sobre item já resolvido;
- feedback “útil”, “não útil” e “não lembrar novamente”;
- explicação de por que o alerta foi criado.

### 14.4. Briefing pré-reunião

O briefing deve combinar agenda e memória, contendo apenas quando disponível:

- objetivo e participantes;
- última interação;
- estado atual de projetos relacionados;
- decisões vigentes;
- pendências abertas;
- mudanças desde o último encontro;
- oportunidades registradas;
- perguntas sugeridas, claramente marcadas como sugestão;
- fontes para cada bloco factual.

---

## 15. Casos de uso prioritários

### UC-01 — Ingerir um Transcript Event

**Ator:** Capture & Ingestion no iPhone ou outro adaptador autorizado.

**Pré-condições:** identidade válida; `capture_id` único; transcrição local válida; somente texto e metadados são enviados.

**Fluxo principal:**

1. Capture & Ingestion envia transcrição, metadados e `capture_id`.
2. Agents autentica, valida o contrato e verifica idempotência.
3. Agents persiste a Raw Memory antes de confirmar o recebimento.
4. Agents retorna `202 Accepted`, `source_id`, `job_id` e `duplicate`.
5. O dispositivo pode excluir o áudio local após essa confirmação durável.
6. Worker executa validação semântica e extrai candidatos e evidências.
7. Memory Manager consolida memória.
8. Índices e Wiki são atualizados como projeções.
9. Evento `ingestion.completed` é emitido.

**Resultado:** conteúdo original preservado e memórias consultáveis com proveniência.

**Exceções:** o mesmo `capture_id` retorna fonte e job existentes; schema inválido é rejeitado; conteúdo semanticamente vazio preserva a fonte mas não gera memória; baixa confiança gera revisão; falha recuperável é reprocessada. Agents nunca solicita o áudio para retry.

### UC-02 — Perguntar sobre a memória

**Ator:** usuário por qualquer UI.

**Fluxo principal:**

1. usuário pergunta “O que ficou decidido sobre o Projeto X?”;
2. backend resolve Projeto X no workspace;
3. recupera decisões, fatos atuais, histórico e evidências;
4. sintetiza uma resposta temporal;
5. valida que as afirmações possuem fontes;
6. retorna resposta, fontes e incertezas.

**Resultado:** resposta fundamentada, sem ocultar conflitos ou falta de dados.

### UC-03 — Consultar pendências com uma pessoa

**Ator:** usuário.

**Fluxo principal:** resolve a pessoa, consulta itens operacionais abertos, ordena por urgência e agrupa por projeto. Itens sem responsável ou prazo confirmado são sinalizados como incertos.

**Resultado:** lista acionável com contexto e fonte de cada compromisso.

### UC-04 — Corrigir uma memória

**Ator:** usuário autorizado.

**Fluxo principal:**

1. usuário informa que duas entidades são a mesma pessoa ou corrige um fato;
2. backend apresenta impacto da correção;
3. usuário confirma;
4. sistema registra autoria e motivo;
5. projeções e índices são reconstruídos;
6. respostas futuras priorizam a correção.

**Resultado:** memória corrigida sem apagar histórico ou evidências originais.

### UC-05 — Detectar uma mudança

**Ator:** pipeline de ingestão.

**Fluxo principal:** nova fonte informa prazo de novembro; memória possui prazo de setembro; sistema verifica entidade, datas e evidência; marca o fato anterior como substituído e o novo como atual.

**Resultado:** consultas presentes usam novembro e consultas históricas continuam encontrando setembro.

### UC-06 — Tratar informação conflitante

**Ator:** pipeline de ingestão.

**Fluxo principal:** duas fontes confiáveis registram valores incompatíveis sem ordem temporal clara; sistema marca disputa, mantém ambas, reduz confiança da visão consolidada e pode solicitar revisão.

**Resultado:** nenhuma falsa certeza é criada.

### UC-07 — Gerar briefing antes de reunião

**Ator:** scheduler.

**Pré-condições:** integração de agenda autorizada e reunião dentro da janela configurada.

**Fluxo principal:** identifica participantes e organização, recupera interações e pendências, gera briefing citado, deduplica e envia ao canal preferido.

**Resultado:** usuário recebe contexto relevante no momento certo.

### UC-08 — Preparar e enviar um follow-up

**Ator:** usuário e Action Engine.

**Fluxo principal:** agente cria rascunho com base na reunião; usuário revisa; backend cria pedido de ação com destinatários e conteúdo exatos; após aprovação, envia uma única vez e armazena recibo.

**Resultado:** mensagem externa corresponde exatamente ao conteúdo aprovado.

### UC-09 — Esquecer ou excluir dados

**Ator:** usuário autorizado.

**Fluxo principal:** usuário seleciona fonte, entidade ou escopo; backend calcula dependências; confirma impacto; remove ou anonimiza conforme política; apaga objetos e índices derivados; registra auditoria mínima permitida.

**Resultado:** dado deixa de ser recuperável e não reaparece em reprocessamentos.

### UC-10 — Reprocessar com um extrator novo

**Ator:** operação do sistema.

**Fluxo principal:** seleciona fontes e versão do pipeline; executa em paralelo lógico sem publicar; compara resultados; preserva correções; promove somente a versão validada; reconstrói projeções.

**Resultado:** evolução do modelo sem perda de controle sobre a memória acumulada.

### UC-11 — Refletir sobre um período

**Ator:** usuário.

**Exemplo:** “Quais decisões deste ano foram alteradas?”

**Fluxo principal:** consulta transições `supersedes`, restringe o período, agrupa por entidade, recupera evidências e produz análise. O sistema diferencia contagem objetiva de interpretação.

**Resultado:** síntese longitudinal auditável.

---

## 16. API de aplicação

As rotas abaixo representam capacidades, não uma definição final de protocolo.

### Ingestão

- `POST /v1/sources` — registrar fonte e iniciar processamento;
- `GET /v1/ingestions/{job_id}` — consultar estágio e falhas;
- `POST /v1/sources/{source_id}/reprocess` — iniciar nova versão;
- `GET /v1/sources/{source_id}` — obter fonte e memórias derivadas.

### Memória

- `GET /v1/entities` — buscar entidades;
- `GET /v1/entities/{id}` — visão consolidada e timeline;
- `GET /v1/memories` — busca estruturada;
- `POST /v1/memories/{id}/corrections` — corrigir memória;
- `POST /v1/entities/merge` — unir entidades com auditoria;
- `POST /v1/entities/{id}/split` — desfazer união;
- `DELETE /v1/sources/{id}` — excluir conforme política.

### Consulta e conversa

- `POST /v1/memory/query` — consulta stateless da memória com fontes;
- `POST /v1/conversations` — iniciar sessão;
- `POST /v1/conversations/{id}/messages` — continuar conversa;
- `GET /v1/memory/queries/{id}/sources` — inspecionar evidências de uma consulta registrada;

### Ações

- `GET /v1/skills` — Skills disponíveis para o usuário;
- `POST /v1/action-requests` — criar preview de ação;
- `POST /v1/action-requests/{id}/approve` — aprovar versão exata;
- `POST /v1/action-requests/{id}/reject` — rejeitar;
- `GET /v1/action-executions/{id}` — consultar resultado e recibo.

### Proatividade

- `GET /v1/briefings` — listar briefings;
- `POST /v1/briefings/generate` — gerar sob demanda;
- `GET /v1/proactivity/preferences` — consultar preferências;
- `PUT /v1/proactivity/preferences` — alterar horários, limites e categorias;
- `POST /v1/proactive-items/{id}/feedback` — registrar utilidade.

### Regras da API

- identificador de workspace vem da identidade autenticada, não é confiado ao corpo da requisição;
- mutações aceitam chave de idempotência;
- listas usam paginação por cursor;
- datas são armazenadas em UTC com fuso original quando relevante;
- erros têm código estável, mensagem segura e correlation ID;
- operações longas retornam job;
- contratos são versionados;
- nenhum endpoint retorna conteúdo de outro workspace.

---

## 17. Eventos internos

Eventos permitem desacoplar os módulos sem perder confiabilidade:

- `source.accepted`;
- `transcript.accepted`;
- `ingestion.started`;
- `extraction.completed`;
- `memory.changed`;
- `memory.review_requested`;
- `ingestion.completed`;
- `ingestion.failed`;
- `action.requested`;
- `action.approved`;
- `action.executed`;
- `action.failed`;
- `calendar.event_upcoming`;
- `briefing.generated`;
- `proactive_item.delivered`.

Cada evento deve possuir `event_id`, `event_type`, `occurred_at`, `workspace_id`, `correlation_id`, versão do schema e payload mínimo. Consumidores precisam ser idempotentes. Eventos carregam apenas os identificadores e dados textuais mínimos necessários; nunca carregam ou referenciam áudio no fluxo de voz do MVP.

---

## 18. Segurança, privacidade e confiança

### 18.1. Isolamento

- toda tabela e índice possui `workspace_id`;
- autorização é aplicada no serviço e, quando possível, também no banco;
- caches usam chave com tenant;
- jobs carregam escopo assinado ou revalidam identidade;
- testes automatizados tentam acessos cruzados entre workspaces.

### 18.2. Proteção de dados

- criptografia em trânsito e em repouso;
- credenciais em cofre de segredos;
- logs sem transcrições completas, tokens ou segredos;
- políticas configuráveis de retenção;
- exportação e exclusão dos dados do usuário;
- registro de consentimento para captura, transcrição e integrações, conforme o contexto de uso;
- política explícita sobre envio de dados a provedores de IA.

Agents não recebe nem armazena áudio. A proteção e a exclusão do áudio temporário pertencem a Capture & Ingestion; o backend deve proteger a transcrição como dado potencialmente sensível.

### 18.3. Prompt injection e conteúdo não confiável

Transcrições, e-mails e documentos são dados, não instruções. O runtime deve:

- delimitar conteúdo recuperado;
- não conceder ferramentas por instruções encontradas nas fontes;
- validar toda chamada de Skill fora do modelo;
- filtrar destinos e URLs quando necessário;
- exigir aprovação conforme risco;
- limitar exfiltração de dados por escopo e política.

### 18.4. Exclusão

Excluir apenas a projeção da Wiki é insuficiente. O processo deve localizar transcrição, segmentos, embeddings, evidências, memórias derivadas e caches. Backups seguem uma janela documentada de expiração. Memórias sustentadas por outras fontes podem permanecer, mas sem a evidência excluída e com confiança recalculada.

### 18.5. Auditoria

Registrar:

- quem acessou ou alterou memória sensível;
- qual pipeline criou ou modificou uma memória;
- merges, splits e correções;
- aprovações e ações externas;
- política de autonomia aplicada;
- versão de modelo e prompt por execução, sem registrar raciocínio interno sensível.

---

## 19. Observabilidade e operação

### 19.1. Métricas técnicas

- latência e taxa de erro por endpoint;
- profundidade e idade da fila;
- tempo total por ingestão e por estágio;
- retries e dead letters;
- custo, tokens e latência por chamada de modelo;
- taxa de schema inválido;
- atualização dos índices;
- taxa de falha e duplicação de Skills.

### 19.2. Métricas de qualidade

- precisão e cobertura de entidades;
- precisão de decisões e action items;
- taxa de merges incorretos;
- precisão temporal atual/histórica;
- respostas com todas as afirmações citadas;
- groundedness das respostas;
- perguntas respondidas sem evidência suficiente;
- taxa de correção manual;
- utilidade de briefings;
- alertas ignorados ou desativados.

### 19.3. Tracing

Um `correlation_id` deve conectar requisição, job, chamadas de modelo, operações de memória, buscas, resposta e eventual ação. Isso permite investigar uma resposta incorreta até a fonte e a versão do pipeline.

### 19.4. Limites e degradação

- timeouts por dependência;
- circuit breaker para provedores instáveis;
- retries com backoff apenas em falhas recuperáveis;
- orçamento por execução;
- fallback de modelo quando compatível com o schema;
- consulta ainda disponível se o pipeline de ingestão estiver atrasado;
- resposta parcial marcada como tal quando uma fonte estiver indisponível.

---

## 20. Estratégia de testes e avaliação

### 20.1. Testes determinísticos

- regras temporais e transições de estado;
- idempotência de ingestão e ações;
- autorização e isolamento por workspace;
- merge/split de entidades;
- exclusão em todas as projeções;
- schemas de entrada/saída;
- ordenação e filtros de retrieval;
- retries, timeout e atomicidade entre fonte, job e eventos;
- aprovação da versão exata de uma ação.

### 20.2. Avaliações de IA

Manter um dataset versionado com transcrições e respostas esperadas para avaliar:

- extração de entidade, decisão, prazo, responsável e negação;
- distinção entre conteúdo afirmado, especulativo e negado;
- resolução de aliases ambíguos;
- duplicata versus atualização;
- conflito versus sucessão temporal;
- recuperação de evidência;
- fidelidade e completude de respostas;
- recusa correta quando não há evidência;
- resistência a prompt injection em conteúdo ingerido.

Avaliações devem usar métricas automáticas e revisão humana amostral. Alterações de prompt, modelo, chunking ou ranking só devem ser promovidas após comparação com a baseline.

### 20.3. Casos obrigatórios do dataset

- “Carlos não ficou responsável” para testar negação;
- datas relativas como “sexta que vem”, preservando data e fuso da fonte;
- dois Carlos em empresas diferentes;
- prazo alterado de setembro para novembro;
- conversa especulativa que não representa decisão;
- promessa sem responsável claro;
- fonte contraditória;
- transcrição repetida;
- correção do usuário seguida de reprocessamento;
- pergunta cuja resposta não existe.

### 20.4. Testes ponta a ponta

Usar fontes sintéticas, ambientes isolados e adaptadores externos fake. Verificar o caminho completo entre ingestão, memória, consulta, citação, aprovação e ação. Ações reais não devem ser disparadas em suites automatizadas.

### 20.5. Gates de qualidade entre fases

Antes do piloto, cada métrica crítica deve possuir baseline, conjunto de avaliação, limite mínimo aprovado e orçamento máximo de regressão. A progressão segue estas regras:

- Retrieval só é promovido quando entidade, temporalidade, negação e evidência atingirem a baseline de memória acordada;
- respostas só são promovidas quando todas as afirmações factuais importantes apontarem para evidências válidas e a taxa de resposta sem suporte estiver abaixo do limite acordado;
- proatividade só é ativada quando pendências, participantes e datas atingirem os limites de precisão definidos para o piloto;
- Skills com escrita só são habilitadas depois que aprovação, idempotência e correspondência exata entre preview e execução passarem integralmente nos testes determinísticos;
- qualquer regressão relevante bloqueia a fase dependente, mesmo que latência ou custo tenham melhorado.

Os números devem ser definidos usando o dataset piloto antes da implementação da fase dependente. “Parece bom” não é critério de promoção.

---

## 21. Requisitos não funcionais iniciais

Os valores exatos devem ser ajustados após testes, mas o produto deve nascer com objetivos explícitos:

- nenhuma ação externa duplicada após retry;
- nenhuma resposta cruza dados entre workspaces;
- 100% das afirmações factuais importantes retornam referência interna de evidência;
- fontes aceitas ficam duráveis antes do início do processamento;
- reprocessamento é possível sem perder correções;
- consulta simples possui caminho síncrono e ingestão longa é assíncrona;
- falhas são observáveis e recuperáveis;
- usuário consegue exportar, corrigir e excluir seus dados;
- cada mudança de memória é auditável;
- budgets de modelo, passos e tempo são aplicados pelo runtime.

Metas de latência, disponibilidade e retenção devem ser definidas quando o canal inicial e a escala esperada forem conhecidos.

---

## 22. Plano incremental de desenvolvimento

### Fase 0 — Fundação

Entregas:

- modelo de domínio e fronteira por workspace;
- autenticação e autorização;
- PostgreSQL/Supabase com `pgvector` e tabela de jobs;
- Model Gateway;
- contratos de Transcript Event, fonte, job, evidência e erro;
- auditoria e tracing básicos.

Critério de saída: uma fonte é aceita de forma idempotente, preservada e acompanhada por job do início ao fim.

### Fase 1 — Ingestão e Raw Memory

Entregas:

- recebimento idempotente de Transcript Events por `capture_id`;
- persistência durável antes da confirmação ao dispositivo;
- segmentação com offsets de caracteres e timestamps apenas quando fornecidos;
- Validation Gate semântico;
- pipeline versionado;
- retries e reprocessamento;
- visualização do estado da ingestão.

Critério de saída: fontes duplicadas não geram processamento ou memória duplicados e falhas podem ser diagnosticadas.

### Fase 2 — Memória semântica e operacional

Entregas:

- extração estruturada;
- entidades e aliases;
- `Source`, `Entity`, `Fact`, `Episode`, `ActionItem`, `Evidence` e `Correction`;
- decisões inicialmente representadas como tipo de fato ou episódio;
- evidência por item;
- Memory Manager com create, confirm, supersede e dispute;
- tela/API de revisão e correção.

Critério de saída: o dataset de avaliação atinge a baseline acordada e mudanças temporais preservam histórico.

### Fase 3 — Retrieval e conversa fundamentada

Entregas:

- busca estruturada, full-text e vetorial com `pgvector`;
- reranking simples;
- respostas com fontes e incerteza;
- timeline de entidade;
- conversa curta sem promoção automática para memória.

Critério de saída: perguntas do conjunto de aceitação são respondidas corretamente e sem alucinar quando faltam dados.

### Fase 4 — Wiki e experiência de controle

Entregas:

- projeções de pessoas, empresas e projetos;
- templates idealizados, mas não obrigatórios, por tipo de entidade;
- seções genéricas e subtópicos criados pelo Wiki Agent;
- versionamento da estrutura e justificativa para seções criadas pelo agente;
- histórico, decisões e pendências;
- merge/split de entidades;
- exportação e exclusão;
- feedback sobre respostas e memórias.

Critério de saída: usuário consegue inspecionar e corrigir o caminho da fonte até a Wiki; o agente consegue adicionar um subtópico relevante sem migração de schema; e nenhum conteúdo factual da projeção existe sem referência à memória e à evidência correspondentes.

### Fase 5 — Proatividade controlada

Entregas:

- integração de calendário somente leitura;
- briefing sob demanda;
- briefing pré-reunião;
- preferências, limites, deduplicação e feedback.

Pré-condição de entrada: as baselines mínimas de qualidade de memória, retrieval e resposta foram atingidas.

Critério de saída: briefings são avaliados como úteis e não geram notificações repetidas ou fora da janela permitida.

### Fase 6 — Skills e ações

Entregas:

- catálogo de Skills;
- runtime limitado;
- dry-run e preview;
- aprovação explícita;
- idempotência e recibos;
- primeira integração de escrita, inicialmente em modo rascunho.

Pré-condição de entrada: a qualidade das fases anteriores está estável e a proatividade não amplifica erros relevantes.

Critério de saída: nenhuma ação ocorre sem a política correta e retries não duplicam efeitos externos.

---

## 23. Critérios de aceite do MVP

O MVP de Agents está pronto para uso piloto quando:

1. uma transcrição pode ser enviada novamente sem duplicar conteúdo ou memória;
2. transcrição original e text spans podem ser abertos a partir da memória derivada;
3. pessoas, empresas, projetos, decisões e pendências são extraídos em formato estruturado;
4. aliases comuns são resolvidos e casos ambíguos não são fundidos silenciosamente;
5. uma alteração de prazo substitui a visão atual sem destruir a anterior;
6. perguntas sobre presente e passado retornam versões temporais corretas;
7. respostas factuais apresentam fontes reais;
8. ausência ou conflito de informação é comunicado;
9. usuário consegue corrigir fato e entidade;
10. reprocessamento preserva correções manuais;
11. exclusão remove fonte e derivados dos sistemas de consulta;
12. traces permitem explicar como uma resposta foi produzida;
13. testes de isolamento impedem vazamento entre workspaces;
14. custo e latência de modelos são medidos por caso de uso;
15. a qualidade é comparada contra um dataset versionado antes de cada mudança relevante.

---

## 24. Decisões arquiteturais recomendadas

1. **Começar com monólito modular e workers**, não microsserviços.
2. **Usar banco relacional como fonte de verdade** e índices como projeções reconstruíveis.
3. **Não adotar banco de grafo por antecipação**; validar a necessidade com consultas reais.
4. **Implementar workflows especializados antes de múltiplos agentes autônomos**.
5. **Separar extração de persistência** por schemas e serviços de domínio.
6. **Tratar temporalidade e proveniência no primeiro modelo de dados**, pois adicioná-las depois exige reconstruir a memória.
7. **Exigir evidência para memória derivada**.
8. **Tratar correção e exclusão como casos de uso de primeira classe**.
9. **Começar Skills externas em leitura, sugestão ou rascunho**.
10. **Versionar pipeline, prompt, modelo e schema** para permitir avaliação e reprocessamento.
11. **Receber somente transcrição e metadados no fluxo de voz**; áudio permanece fora de Agents.
12. **Tratar a Wiki como projeção reconstruível**, nunca como memória primária editada pelo modelo.
13. **Persistir no MVP apenas o subconjunto essencial do domínio**, expandindo-o conforme consultas reais.
14. **Separar confiança de extração, confiabilidade da fonte e confiança da memória**.
15. **Validar deterministicamente temporalidade e persistência propostas pelo LLM**.
16. **Usar templates de Wiki como orientação, não como taxonomia fechada**; o Wiki Agent pode criar subtópicos quando sustentados pela memória.

---

## 25. Questões que precisam de decisão de produto

Estas questões não impedem a fundação técnica, mas alteram políticas e critérios:

- o sistema será inicialmente individual ou permitirá memória compartilhada por equipe?
- qual canal será usado no piloto?
- por quanto tempo as transcrições e metadados serão preservados no backend?
- qual mecanismo do iPhone será usado primeiro: Shortcut/Atalho, widget, Action Button ou aplicativo dedicado?
- quais mecanismos de transcrição no dispositivo atendem aos requisitos de idioma, duração e privacidade do piloto?
- quais categorias de dado exigem confirmação antes de virar memória atual?
- o usuário poderá marcar uma conversa como “não memorizar” antes da ingestão?
- quais tipos de memória podem ser compartilhados entre pessoas?
- qual será a primeira integração externa de leitura?
- qual será a primeira Skill com efeito de escrita?
- briefings serão gerados apenas sob demanda ou enviados automaticamente no piloto?

As respostas devem ser registradas como decisões arquiteturais e convertidas em políticas configuráveis sempre que houver variação por usuário ou workspace.

---

## 26. Definição do core

O verdadeiro core da aplicação não é um prompt, um modelo específico ou uma interface de chat. É a combinação de:

```text
fontes preservadas
+ memória estruturada e temporal
+ proveniência
+ recuperação híbrida
+ regras de autorização
+ execução observável e idempotente
```

Se esses elementos forem sólidos, o produto poderá trocar modelos, adicionar canais e evoluir de segundo cérebro para assistente executivo e agente pessoal sem perder seu principal ativo: uma memória confiável, controlável e útil ao longo do tempo.
