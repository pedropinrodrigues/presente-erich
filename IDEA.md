# Arquitetura do Sistema — Assistente Pessoal com Memória de Longo Prazo

## 1. Visão Geral

O projeto tem como objetivo desenvolver um assistente pessoal inteligente com memória de longo prazo, capaz de capturar informações do cotidiano do usuário, transformar essas informações em conhecimento estruturado e utilizar essa memória para auxiliar continuamente na gestão de atividades pessoais e profissionais.

A proposta vai além de um chatbot tradicional.

O sistema deve funcionar como uma combinação de:

* Segundo cérebro digital
* Memória pessoal de longo prazo
* Memória organizacional
* Wiki dinâmica
* Assistente executivo
* Agente capaz de executar ações
* Sistema proativo de acompanhamento

O principal mecanismo inicial de captura será a fala. No MVP, o iPhone do usuário será responsável por gravar o áudio temporariamente, transcrevê-lo no próprio dispositivo e enviar somente o texto ao backend. O usuário poderá registrar reuniões, conversas, pensamentos, decisões, ideias e outros acontecimentos relevantes sem precisar estruturar manualmente essas informações.

O áudio não será um ativo permanente do sistema. Ele será apenas um meio temporário, mantido localmente durante o processo necessário para transformar fala em texto. No fluxo normal, o backend não deverá receber nem armazenar arquivos de áudio.

O sistema será responsável por transformar essas entradas em conhecimento persistente.

A arquitetura será dividida em três grandes componentes:

1. Capture & Ingestion
2. Agents
3. User Interface

A separação busca garantir independência entre captura, inteligência e interação.

---

## 2. Princípio Arquitetural

A arquitetura deve seguir o princípio:

Capturar → Transcrever → Validar → Entender → Memorizar → Recuperar → Raciocinar → Agir

O fluxo conceitual é:

```text
                 USUÁRIO
                    │
          ┌─────────┴─────────┐
          │                   │
          ▼                   ▼
 CAPTURE & INGESTION          UI
          │                   │
          │ Transcript Event  │
          └──────────┬────────┘
                     ▼
                  AGENTS
                     │
       ┌─────────────┼─────────────┐
       │             │             │
       ▼             ▼             ▼
    MEMORY       REASONING       SKILLS
       │                           │
       ▼                           ▼
 Collective Memory          Sistemas Externos
```

Duas decisões arquiteturais importantes são:

A camada Agents não deve depender da implementação da interface do usuário.

A camada Agents também não deve saber nem depender de como uma transcrição foi produzida. Sua fronteira de entrada será baseada em texto e metadados, nunca no arquivo de áudio.

Isso permite que diferentes interfaces sejam adicionadas ou substituídas sem modificar o núcleo inteligente do sistema.

Por exemplo, o usuário poderá futuramente interagir através de:

* Web App
* Aplicativo mobile
* WhatsApp
* Telegram
* Chat
* Interface por voz
* E-mail
* Notificações
* Smartwatch

Todas essas interfaces deverão utilizar a mesma camada agêntica.

---

## 3. Componentes Principais

### 3.1. Capture & Ingestion

Responsabilidade

A camada Capture & Ingestion é responsável por transformar fala em texto e entregar a transcrição de forma confiável à camada Agents.

Ela representa uma das principais portas de entrada de informação para o sistema.

Seu objetivo é tornar a captura de conhecimento extremamente simples para o usuário.

No MVP, essa camada será executada no iPhone. A interação ideal deverá utilizar um Shortcut/Atalho, widget ou Action Button para reduzir a captura a uma ação de início e outra de encerramento.

A experiência ideal deve ser:

```text
Usuário fala
      ↓
Áudio é capturado temporariamente no iPhone
      ↓
Transcrição é realizada no dispositivo
      ↓
Validação básica
      ↓
Somente texto e metadados são enviados
      ↓
Backend confirma recebimento
      ↓
Áudio local é descartado
```

O usuário não deve precisar organizar manualmente o conteúdo.

---

### 3.2. Responsabilidades de Capture & Ingestion

A camada executada no dispositivo será responsável por:

* Iniciar e encerrar a gravação
* Manter o áudio apenas temporariamente no dispositivo
* Gerar a transcrição no próprio dispositivo
* Identificação de data e horário
* Identificação do dispositivo de origem
* Identificação de idioma e duração quando disponíveis
* Executar uma validação mínima da captura
* Salvar temporariamente a transcrição
* Enviar automaticamente somente texto e metadados para Agents
* Manter uma fila local de transcrições ainda não confirmadas
* Realizar retry em caso de falha de comunicação
* Excluir o áudio após confirmar que existe uma transcrição válida e que ela foi entregue

Um Transcript Event enviado para Agents poderá conter:

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

O `capture_id` deverá ser único e funcionar como chave de idempotência. Se o dispositivo repetir o envio após uma falha ou ausência de confirmação, o backend deverá reconhecer que se trata da mesma captura e não iniciar um segundo processamento.

---

### 3.3. O que Capture & Ingestion não deve fazer

Capture & Ingestion não deve decidir:

* Quais informações são importantes
* Quais informações devem virar memória
* Quem são as pessoas relevantes
* Quais decisões foram tomadas
* Quais tarefas surgiram
* Como atualizar a Wiki
* Como responder perguntas
* Que ações devem ser executadas

Essas responsabilidades pertencem à camada Agents.

A separação deve ser:

Capture & Ingestion = transformar fala em texto e entregar
Agents = transformar texto em conhecimento, memória e ações

---

### 3.4. Validation Gate

Antes que uma captura seja enviada para processamento agêntico, deverá existir um Validation Gate no dispositivo.

A validação básica poderá considerar em conjunto:

* Existência de uma transcrição
* Existência de texto ou fala reconhecida
* Quantidade de palavras
* Duração da gravação
* Integridade mínima dos metadados

Duração não poderá ser usada isoladamente como critério de descarte. Uma gravação curta como:

“Lembrar de falar com Carlos amanhã sobre o contrato.”

pode representar uma memória válida e importante.

A validação no dispositivo deverá eliminar somente falhas inequívocas, como transcrição vazia ou arquivo corrompido. A relevância e a validade semântica do conteúdo serão avaliadas posteriormente por Agents. Conteúdo curto, incompleto ou ambíguo não deverá ser descartado automaticamente apenas por parecer pouco expressivo.

---

### 3.5. Tratamento de falhas e descarte do áudio

O áudio local não poderá ser apagado antes que exista uma transcrição válida.

O fluxo deverá seguir:

```text
Áudio local temporário
        ↓
Transcrição no dispositivo
        ↓
Validation Gate básico
        ↓
Transcrição salva temporariamente
        ↓
POST para Agents
        ↓
Backend persiste e confirma recebimento
        ↓
Dispositivo marca a entrega como concluída
        ↓
Áudio local é excluído
```

Se o envio falhar:

```text
Falha
  ↓
Manter transcrição em fila local
  ↓
Retry automático com o mesmo capture_id
```

Depois que uma transcrição válida existir, os retries deverão utilizar o texto salvo. Não deverão exigir uma nova transcrição nem depender novamente do áudio.

A confirmação do backend significa que o evento foi persistido de forma durável e poderá ser processado de maneira assíncrona. Ela não precisa aguardar a extração e a consolidação completa da memória.

---

### 3.6. Privacidade e retenção no dispositivo

O áudio deverá permanecer somente pelo tempo necessário para gerar uma transcrição válida. Após a confirmação durável do backend, ele deverá ser excluído automaticamente.

Se o envio estiver indisponível, a transcrição permanecerá na fila local para retry e o áudio será mantido temporariamente. No MVP, o áudio somente será excluído depois da confirmação de entrega pelo backend. Mesmo enquanto o áudio ainda existir, os retries deverão utilizar a transcrição já salva, e não executar novamente o processo de transcrição.

O dispositivo deverá proteger áudio e transcrições temporárias usando os mecanismos de segurança disponíveis no iPhone e não deverá expor esse material a outros aplicativos sem autorização.

---

### 3.7. Independência do dispositivo de captura

Agents receberá sempre o mesmo contrato conceitual de Transcript Event. Isso permitirá substituir ou adicionar dispositivos e canais sem alterar o núcleo agêntico:

```text
iPhone ──────┐
Omi ─────────┤
Plaud ───────┤
WhatsApp ────┼──→ Transcript Event → Agents
Desktop ─────┤
Meeting Bot ─┘
```

Cada adaptador de captura será responsável por produzir texto e metadados no contrato esperado. Agents não deverá distinguir a tecnologia de transcrição para executar sua lógica central.

---

## 4. Agents

### 4.1. Visão Geral

A camada Agents representa o núcleo cognitivo do sistema.

É responsável por transformar informações brutas em conhecimento persistente e posteriormente utilizar esse conhecimento para auxiliar o usuário.

Suas quatro capacidades fundamentais são:

Entender, lembrar, raciocinar e agir.

Dentro dessa camada existirão diferentes responsabilidades lógicas.

Inicialmente, elas podem ser implementadas dentro do mesmo serviço, mas devem permanecer conceitualmente separadas.

No MVP, Agents deverá ser implementado como um monólito modular com workflows especializados, e não como múltiplos agentes autônomos. Modelos de IA poderão extrair, classificar, sugerir operações de memória e planejar consultas, mas não poderão persistir dados nem executar ações diretamente: serviços de domínio deverão validar evidências, schemas, temporalidade, autorização e idempotência antes de qualquer mudança.

O documento `AGENTS-BACKEND/README.md` detalha os contratos, o modelo de dados, as regras de segurança e a implantação dessa camada. Esta seção mantém a visão arquitetural do produto.

A arquitetura interna proposta é:

```text
Agents
│
├── Knowledge Ingestion
│
├── Memory
│
├── Retrieval / Reasoning
│
├── Skills / Actions
│
└── Scheduler / Proactivity
```

---

## 5. Knowledge Ingestion

### 5.1. Objetivo

O módulo de Knowledge Ingestion recebe texto e metadados provenientes das diferentes fontes e transforma conteúdo não estruturado em informação estruturada.

Inicialmente, sua principal fonte será o Transcript Event produzido no iPhone por Capture & Ingestion.

O módulo não recebe áudio, não executa transcrição e não depende de como o texto foi produzido.

Futuramente poderá receber:

* E-mails
* Documentos
* Mensagens
* Calendário
* CRM
* Anotações
* Arquivos
* Outras fontes externas

---

### 5.2. Extração de informação

Uma transcrição poderá conter:

Conversei hoje com Carlos sobre o Projeto Alfa.
Decidimos entregar a primeira fase até setembro.
Carlos ficou responsável por enviar a documentação técnica.
Também discutimos a possibilidade de expandir o projeto para Recife.

O sistema deverá identificar automaticamente:

Pessoas

Carlos

Projetos

Projeto Alfa

Decisões

Primeira fase será entregue até setembro.

Compromissos

Carlos deverá enviar documentação técnica.

Oportunidades

Possível expansão para Recife.

O objetivo não é simplesmente resumir uma transcrição.

O sistema deve responder:

O que dessa conversa merece ser lembrado no futuro?

---

## 6. Memory

### 6.1. Objetivo

Memory será responsável pela memória persistente de longo prazo.

Esse componente é um dos elementos mais importantes de todo o sistema.

O sistema não deve funcionar apenas como um banco de transcrições.

Ele deverá construir uma representação estruturada do conhecimento acumulado.

Essa estrutura será chamada de:

Collective Memory

ou

Wiki

---

## 7. Collective Memory

A Collective Memory funcionará como uma espécie de Wikipedia privada e dinâmica da vida profissional e organizacional do usuário.

A Wiki é uma projeção dessa memória, e não sua fonte de verdade. Cada informação factual exibida deverá derivar de uma memória estruturada com evidência; a projeção poderá ser reconstruída quando uma fonte, uma correção, uma regra de consolidação ou a organização da página mudar.

Uma estrutura inicial poderia ser:

```text
MEMÓRIA
│
├── Pessoas
│
├── Empresas
│
├── Projetos
│
├── Reuniões
│
├── Decisões
│
├── Compromissos
│
├── Ideias
│
├── Problemas
│
├── Oportunidades
│
└── Conhecimento Institucional
```

Essas entidades não devem existir isoladamente.

Elas precisam estar relacionadas.

Exemplo:

```text
Carlos
   │
   ├── trabalha em → Empresa Alfa
   │
   ├── participa de → Projeto X
   │
   ├── participou de → Reunião 23/07
   │
   └── responsável por → Entrega Y
```

O sistema começa, portanto, a formar um grafo de conhecimento sobre a realidade do usuário.

---

## 8. Exemplo de uma página da Wiki

Uma entidade referente a um cliente poderia possuir:

EMPRESA ALFA
Descrição
Cliente corporativo.
Pessoas relacionadas
- Carlos Silva — CEO
- Ana Santos — Diretora Financeira
Projetos
- Projeto X
- Expansão Recife
Histórico
- Primeiro contato: março/2026
- Proposta enviada: abril/2026
- Negociação retomada: junho/2026
Decisões
- Desconto máximo acordado: 8%
- Implantação prevista inicialmente em 60 dias
Pendências
- Revisar SLA
- Confirmar infraestrutura
Oportunidades
- Possível expansão para Recife
Últimas interações
- Reunião 08/08
- Ligação 12/08

Essa página não precisa ser escrita manualmente.

Ela deve surgir da memória acumulada pelo sistema.

---

## 9. Memória Temporal

Um requisito fundamental será entender que conhecimento muda ao longo do tempo.

Por exemplo:

03/08/2026
Projeto X será entregue em setembro.

Posteriormente:

17/08/2026
O prazo do Projeto X foi alterado para novembro.

O sistema não pode simplesmente armazenar:

Prazo = Setembro
Prazo = Novembro

Ele precisa compreender a evolução.

Uma representação poderia ser:

```text
Projeto X
└── Prazo
    │
    ├── Setembro
    │   ├── registrado: 03/08/2026
    │   └── status: substituído
    │
    └── Novembro
        ├── registrado: 17/08/2026
        └── status: atual
```

Portanto, uma memória deve idealmente possuir:

Fato
Fonte
Data
Confiança
Validade
Status

Isso evita que informações antigas sejam tratadas como verdades permanentes.

---

## 10. Proveniência

Toda memória importante deverá possuir uma fonte.

Exemplo:

Fato:
Prazo atual do Projeto X é novembro.
Fonte:
Reunião de 17/08/2026.
Trecho:
"..."
Confiança:
Alta.

Isso permite que o agente explique:

“Estou dizendo isso porque essa decisão foi registrada na reunião de 17 de agosto.”

Essa característica será fundamental para criar confiança no sistema.

---

## 11. Diferentes tipos de memória

A memória poderá ser dividida conceitualmente em cinco camadas.

### 11.1. Raw Memory

Representa a informação original.

Exemplos:

* Transcrição
* Documento
* E-mail
* Mensagem

Serve como fonte de verdade e material para reprocessamento.

No fluxo de captura por voz do MVP, a transcrição recebida é a Raw Memory persistente. O áudio existe apenas temporariamente no iPhone e não integra a memória do backend.

Consequentemente, o reprocessamento do backend será realizado a partir da transcrição original e de seus metadados, nunca a partir de um arquivo de áudio armazenado no servidor.

---

### 11.2. Semantic Memory

Representa conhecimento extraído.

Exemplos:

Carlos trabalha na Empresa Alfa.
Empresa Alfa é cliente.
Carlos prefere reuniões pela manhã.
Projeto X pertence à Empresa Alfa.

---

### 11.3. Episodic Memory

Representa eventos situados no tempo.

Exemplos:

* Reuniões
* Ligações
* Conversas
* Decisões tomadas
* Mudanças de contexto
* Interações com clientes

Essa camada permite responder com precisão ao que aconteceu, quando e com quem.

---

### 11.4. Operational Memory

Representa informações relacionadas à execução.

Exemplos:

* Decisões
* Pendências
* Compromissos
* Prazos
* Promessas
* Próximas ações
* Problemas em aberto

Essa memória será especialmente importante para o comportamento proativo do sistema.

---

### 11.5. Interaction Memory

Representa o contexto recente de uma conversa necessário para manter continuidade na interação. Ela não deverá ser promovida automaticamente à memória de longo prazo: a promoção exigirá relevância, evidência e uma operação válida do Memory Manager.

---

## 12. Memory Manager

O Memory Manager será responsável por decidir como novas informações modificam a memória existente.

Quando uma nova informação chegar, ele deverá avaliar:

É uma informação nova?
É uma atualização?
É uma duplicata?
Contradiz algo existente?
Substitui alguma informação?
Complementa uma memória existente?
Está relacionada a qual pessoa?
Está relacionada a qual empresa?
Está relacionada a qual projeto?

Por exemplo:

Carlos agora é Diretor Comercial.

Se anteriormente existia:

Carlos é Gerente Comercial.

o sistema deverá compreender que provavelmente ocorreu uma mudança de cargo.

Não deverá simplesmente criar dois fatos independentes.

---

## 13. Entity Resolution

Outro problema fundamental será identificar quando diferentes referências correspondem à mesma entidade.

Exemplo:

Carlos
Carlos Silva
Carlos da Empresa Alfa
C. Silva

podem representar a mesma pessoa.

O sistema deverá possuir mecanismos de Entity Resolution para evitar a criação de várias pessoas diferentes para o mesmo indivíduo.

O mesmo problema ocorre com:

* Empresas
* Projetos
* Produtos
* Locais
* Contratos

---

## 14. Retrieval / Reasoning

Uma vez construída a memória, o agente precisa conseguir consultá-la.

Exemplo:

“O que ficou decidido com Carlos sobre o Projeto X?”

O sistema deverá:

```text
Pergunta
   ↓
Identificar entidades
   ↓
Buscar memórias relevantes
   ↓
Buscar fontes
   ↓
Considerar contexto temporal
   ↓
Raciocinar
   ↓
Gerar resposta
```

Uma resposta poderia ser:

Na reunião de 14 de maio vocês decidiram manter o preço original
desde que a implantação fosse antecipada.
Em 8 de junho Carlos voltou a questionar o prazo.
A última posição registrada foi entrega até 30 de setembro.

A prioridade deve ser sempre utilizar a informação mais recente e confiável, sem perder o histórico.

---

## 15. Retrieval híbrido

A busca não deverá depender exclusivamente de similaridade semântica.

Idealmente serão combinados:

* Busca vetorial
* Busca textual
* Filtros estruturados
* Relações entre entidades
* Datas
* Tipos de memória
* Importância
* Recência

Por exemplo:

“O que ficou pendente com Carlos?”

Não basta buscar documentos semanticamente parecidos com “Carlos”.

O sistema deverá procurar especificamente:

Entity = Carlos
Memory Type = Commitment / Pending Task
Status = Open

---

## 16. Skills / Actions

O agente não deverá apenas responder perguntas.

Ele também poderá executar ações através de Skills.

Skills representam capacidades externas disponíveis para o agente.

Exemplos:

send_email()
send_message()
get_calendar()
create_calendar_event()
search_memory()
generate_briefing()
create_reminder()
search_documents()

Isso transforma o sistema de:

Assistente que sabe

em:

Assistente que sabe e consegue agir.

---

## 17. Controle das ações

A execução de Skills deverá possuir níveis de autonomia.

Por exemplo:

Baixo risco

Buscar memória
Gerar resumo
Consultar calendário

Pode ocorrer automaticamente.

Médio risco

Criar lembrete
Preparar e-mail
Preparar mensagem

Pode exigir revisão.

Alto risco

Enviar e-mail
Enviar mensagem
Alterar compromisso
Cancelar reunião

Deve inicialmente exigir autorização explícita.

A autonomia poderá evoluir conforme o sistema amadurecer.

---

## 18. Scheduler / Proactivity

Um dos objetivos mais importantes é evitar que o sistema seja apenas reativo.

Um chatbot tradicional funciona assim:

```text
Usuário pergunta
       ↓
Sistema responde
```

O assistente proposto deverá também funcionar assim:

```text
Sistema identifica contexto
       ↓
Busca informações relevantes
       ↓
Determina que existe algo importante
       ↓
Interage proativamente com usuário
```

---

## 19. Briefing diário

Um exemplo de comportamento proativo será o briefing diário.

O sistema poderá enviar:

Bom dia.
Três assuntos importantes para hoje:

1. Reunião com Empresa Alfa às 14h.
2. A proposta para Empresa Beta está há 12 dias sem resposta.
3. Há três semanas você comentou que queria conversar com Roberto
   sobre expansão regional. Não encontrei nenhum registro posterior.

O sistema começa a funcionar como um Chief of Staff digital.

---

## 20. Briefing antes de reuniões

Outra funcionalidade importante será preparar o usuário antes de reuniões.

Suponha que o calendário contenha:

14:00 — Reunião Empresa Alfa

O agente poderá consultar automaticamente a memória.

Resultado:

BRIEFING — EMPRESA ALFA
Último contato
24/07/2026
Objetivo
Negociação do contrato X.
Participantes
Carlos — CEO
Ana — Diretora Financeira
Estado atual
Proposta enviada.
Cliente solicitou revisão do SLA.
Última decisão
Não reduzir preço antes de discutir aumento do prazo contratual.
Pendências
- responder questão sobre redundância
- confirmar prazo de implantação
Oportunidade
Carlos mencionou interesse na Unidade B.
Sugestão
Explorar Unidade B antes de discutir desconto.

Esse briefing poderá ser enviado por:

* App
* WhatsApp
* Telegram
* E-mail
* Notificação

A camada Agents não precisa saber qual será a interface definitiva.

---

## 21. Reflexão sobre a própria memória

Uma das capacidades mais interessantes será permitir consultas agregadas sobre longos períodos.

Exemplos:

“Quais ideias de novos negócios eu tive nos últimos seis meses?”

“Quais problemas da empresa mais apareceram nas minhas reuniões?”

“Quais assuntos eu digo que são importantes mas continuo adiando?”

“Quais clientes apresentaram maior quantidade de problemas?”

“Quais oportunidades comerciais surgiram nos últimos três meses?”

“Quais decisões tomadas no início do ano foram posteriormente alteradas?”

Esse tipo de análise transforma memória em inteligência organizacional.

---

## 22. User Interface

A terceira grande camada será a User Interface.

Sua implementação ainda não precisa ser definida.

O princípio fundamental será:

A inteligência não deverá depender da interface.

A UI será responsável apenas pela interação entre usuário e sistema.

---

## 23. Possíveis interfaces

Poderão ser utilizadas:

Aplicativo

Uma interface própria com:

* Chat
* Memória
* Pessoas
* Empresas
* Projetos
* Briefings
* Capturas e transcrições
* Configurações

WhatsApp

O usuário conversa diretamente com o agente.

Exemplo:

Usuário:
O que preciso saber antes da reunião com Carlos?
Agente:
...

Telegram

Pode funcionar de forma semelhante ao WhatsApp e facilitar uma primeira implementação.

Web App

Pode oferecer uma visualização completa da Wiki e do histórico.

Interface híbrida

Uma possibilidade especialmente interessante é combinar:

```text
WhatsApp / App
        │
        │ interação diária
        ▼
      Agents
        │
        ▼
     Web App
        │
        │ exploração profunda
        ▼
      Wiki
```

O usuário poderia utilizar mensagens para interações rápidas e um Web App quando quisesse explorar a memória.

---

## 24. Independência entre componentes

A arquitetura deverá permitir que cada componente evolua independentemente.

```text
CAPTURE & INGESTION
│
│ produz Transcript Event
▼
AGENTS
│
│ produz inteligência / ações
▼
UI
```

Mas a UI também consulta diretamente as capacidades expostas por Agents:

```text
UI
│
▼
Agents API
│
├── Ask
├── Search Memory
├── Get Entity
├── Get Briefing
├── Execute Skill
└── Get Timeline
```

Isso permite trocar a interface sem modificar o núcleo.

---

## 25. Responsabilidades formais

| Componente | Responsabilidade |
| --- | --- |
| Capture & Ingestion | Capturar fala, transcrever no dispositivo e entregar texto |
| Agents | Interpretar, memorizar, raciocinar e agir |
| UI | Permitir interação com o usuário |

Capture & Ingestion não deve

* Interpretar conhecimento
* Atualizar Wiki
* Tomar decisões
* Enviar ou persistir áudio no backend

Agents não deve

* Depender de uma interface específica
* Implementar apresentação visual
* Receber, armazenar ou exigir áudio no fluxo normal
* Depender da tecnologia usada para gerar uma transcrição

UI não deve

* Possuir lógica central de memória
* Implementar inteligência
* Ser fonte de verdade

---

## 26. Arquitetura interna de Agents

A estrutura conceitual completa será:

```text
                         AGENTS
                            │
        ┌───────────────────┼───────────────────┐
        │                   │                   │
        ▼                   ▼                   ▼
 KNOWLEDGE INGESTION      MEMORY            REASONING
        │                   │                   │
        │                   │                   │
        ▼                   ▼                   ▼
   Information         Collective           Retrieval
   Extraction            Memory             + Context
                            │
                            │
                   ┌────────┴────────┐
                   ▼                 ▼
              Semantic           Operational
               Memory               Memory
                            │
                            ▼
                         AGENT
                            │
                  ┌─────────┴─────────┐
                  ▼                   ▼
                SKILLS            SCHEDULER
                  │                   │
                  ▼                   ▼
          External Actions       Proactivity
```

---

## 27. Fluxo completo de ingestão

```text
Usuário grava reunião
        ↓
iPhone captura áudio temporariamente
        ↓
Usuário encerra a gravação
        ↓
Transcrição realizada no dispositivo
        ↓
Validation Gate básico
        ↓
Transcrição salva temporariamente no dispositivo
        ↓
Transcript Event enviado com capture_id
        ↓
Agents persiste a transcrição e confirma recebimento
        ↓
Dispositivo exclui o áudio local
        ↓
Knowledge Ingestion analisa o texto
        ↓
Validação semântica
        ↓
Entidades identificadas
        ↓
Fatos extraídos
        ↓
Memory Manager analisa
        ↓
Entity Resolution
        ↓
Deduplicação
        ↓
Detecção de atualização / conflito
        ↓
Memória atualizada
        ↓
Wiki atualizada
```

---

## 28. Fluxo completo de consulta

```text
Usuário pergunta
"O que ficou pendente com Carlos?"
        ↓
UI
        ↓
Agent
        ↓
Entendimento da pergunta
        ↓
Entity Resolution
        ↓
Carlos → pessoa correta
        ↓
Retrieval
        ↓
Operational Memory
        ↓
Status = pendente
        ↓
Recuperação das fontes
        ↓
Reasoning
        ↓
Resposta
        ↓
UI
```

---

## 29. Fluxo completo de proatividade

```text
Scheduler
    ↓
Detecta reunião futura
    ↓
Identifica participantes
    ↓
Consulta Collective Memory
    ↓
Busca últimas interações
    ↓
Busca decisões
    ↓
Busca pendências
    ↓
Busca oportunidades
    ↓
Gera briefing
    ↓
Seleciona Skill
    ↓
Envia ao usuário
```

---

## 30. Evolução do produto

O desenvolvimento pode ocorrer incrementalmente.

V1 — Segundo Cérebro

Objetivo:

Criar memória a partir de transcrições capturadas com baixa fricção e permitir conversar com ela.

Funcionalidades:

* Captura de áudio temporário no iPhone com baixa fricção
* Transcrição no dispositivo
* Validation Gate local
* Envio idempotente de Transcript Events
* Fila local e retry automático
* Extração de informação
* Memória persistente
* Projeção inicial de Wiki reconstruível a partir da memória e das evidências
* Busca
* Chat sobre a memória

---

V2 — Assistente Executivo

Adicionar:

* Calendário
* Briefing diário
* Briefing pré-reunião
* Identificação de pendências
* Acompanhamento de compromissos
* Alertas contextuais

Essas capacidades deverão ser ativadas apenas depois que as métricas de qualidade de memória, retrieval e respostas com fontes atingirem os limites definidos para o piloto.

---

V3 — Agente Pessoal

Adicionar:

* E-mail
* Mensagens
* Documentos
* CRM
* Skills
* Execução de ações
* Automação

O sistema evolui de:

MEMÓRIA

para:

ASSISTENTE

e posteriormente:

AGENTE

---

## 31. MVP recomendado

O primeiro MVP deverá validar o problema mais importante:

É possível transformar transcrições de falas do cotidiano em uma memória de longo prazo realmente útil?

Portanto, o MVP não precisa inicialmente possuir:

* App mobile
* WhatsApp
* CRM
* Automação complexa
* Múltiplos agentes
* Dezenas de Skills

O MVP deverá executar muito bem:

```text
Captura temporária no iPhone
   ↓
Transcrição no dispositivo
   ↓
Envio de texto
   ↓
Extração
   ↓
Memória
   ↓
Consulta
```

Se essa cadeia funcionar corretamente, todas as demais capacidades podem ser adicionadas posteriormente.

---

## 32. Princípios do projeto

### 32.1. Zero Friction Capture

Registrar uma informação deve exigir o mínimo possível de esforço.

Idealmente:

Apertar um botão e falar.

---

### 32.2. Memory First

A memória é o principal ativo do sistema.

O modelo de IA poderá mudar.

A interface poderá mudar.

O dispositivo e a tecnologia de captura ou transcrição poderão mudar.

A memória acumulada deve permanecer.

---

### 32.3. Source of Truth

Informações importantes devem manter vínculo com sua fonte original.

No fluxo de voz do MVP, essa fonte original persistente é a transcrição recebida e seus metadados. O áudio temporário não faz parte da fonte de verdade do backend.

Nunca deve existir apenas:

"Prazo é novembro."

Deve ser possível recuperar:

Prazo é novembro.
Fonte:
Reunião 17/08/2026.
Substituiu:
Prazo anterior de setembro.

---

### 32.4. Temporal Awareness

O sistema precisa entender que:

A verdade organizacional muda ao longo do tempo.

---

### 32.5. User Control

O usuário deve poder:

* Consultar memórias
* Corrigir informações
* Excluir memórias
* Confirmar informações importantes
* Controlar ações externas
* Definir níveis de autonomia

---

### 32.6. Interface Independence

O núcleo agêntico não deve depender de:

* WhatsApp
* Aplicativo
* Web
* Telegram
* Voz

Esses elementos são canais de interação.

---

### 32.7. Progressive Autonomy

Inicialmente:

```text
Agente sugere
      ↓
Usuário aprova
      ↓
Agente executa
```

Posteriormente, determinadas ações de baixo risco poderão ser automatizadas.

---

## 33. Visão de longo prazo

O objetivo final não é construir apenas um sistema que armazena transcrições.

O objetivo é criar uma memória digital viva.

Com o passar do tempo, o sistema deverá conhecer:

* Pessoas
* Relações
* Empresas
* Clientes
* Projetos
* Decisões
* Problemas
* Ideias
* Preferências
* Compromissos
* Oportunidades
* Histórico organizacional

Isso permitirá que o agente compreenda não apenas:

“O que foi dito?”

mas também:

“O que aconteceu?”

“O que mudou?”

“O que continua pendente?”

“O que é importante agora?”

“O que deveria ser feito em seguida?”

A evolução conceitual do produto pode ser resumida como:

```text
Captura local
   ↓
Transcrição no dispositivo
   ↓
Memória
   ↓
Segundo Cérebro
   ↓
Assistente Executivo
   ↓
Chief of Staff Digital
   ↓
Agente Pessoal
```

O diferencial fundamental estará na capacidade de transformar informações dispersas do cotidiano em uma Collective Memory estruturada, temporal, rastreável e acionável.

Essa memória deverá funcionar como o principal ativo do sistema e como base para todas as futuras capacidades agênticas.
