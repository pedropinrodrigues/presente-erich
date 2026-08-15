# User Interface — Especificação dos clientes

## 1. Propósito

A interface torna a memória e as capacidades do assistente utilizáveis. No MVP de interface, o canal é o **WhatsApp**. Ele deve permitir fazer perguntas, explorar conhecimento, revisar fatos e, futuramente, aprovar ações, sem assumir o papel de fonte de verdade ou de núcleo de inteligência.

Web, app, Telegram, e-mail e notificações podem ser adicionados depois. Todos consomem os mesmos casos de uso expostos por Agents & Backend.

## 2. Responsabilidades

- autenticar e identificar o usuário/workspace;
- iniciar ou encaminhar capturas ao fluxo apropriado;
- enviar perguntas, comandos, correções e aprovações;
- apresentar respostas com fontes, confiança e contexto temporal;
- exibir entidades, linhas do tempo, pendências e briefings;
- permitir correção, exclusão e controle de autonomia;
- comunicar estados assíncronos, erros e confirmações de modo claro.

Além disso, o adaptador de WhatsApp deve receber webhooks, validar sua origem, associar o identificador do remetente ao usuário/workspace correto e transformar mensagens em comandos normalizados para Agents. Ele não deve consultar o banco de memória diretamente.

A interface não deve extrair conhecimento, resolver entidades, consolidar memória, decidir políticas de autorização ou persistir regras de domínio por conta própria.

## 3. Experiência do MVP

O primeiro cliente é o WhatsApp. Ele precisa suportar três jornadas:

1. **Consultar:** perguntar algo como “O que ficou pendente com Carlos?” e receber resposta com evidências.
2. **Explorar:** abrir pessoas, empresas, projetos ou uma linha do tempo para entender relações e mudanças.
3. **Corrigir:** confirmar, corrigir ou excluir uma memória quando a representação não estiver correta.

Uma tela de captura própria não é requisito do MVP se o iPhone entregar `Transcript Events` diretamente. Pelo WhatsApp, o sistema pode confirmar o recebimento e o processamento da captura, mas não precisa receber áudio no backend.

### Fluxo do canal

```text
Usuário no WhatsApp → webhook validado → adaptador do canal
→ identidade/workspace → Agents API → resposta estruturada
→ mensagem formatada → WhatsApp
```

O adaptador é responsável por formato de mensagem, limites do canal, webhook e entrega. Agents retorna dados de domínio e evidências, não texto acoplado ao WhatsApp.

## 4. Contrato com Agents & Backend

A UI consome uma API orientada a casos de uso, sem conhecer tabelas ou detalhes internos. Capacidades iniciais:

| Caso de uso | Resultado esperado |
| --- | --- |
| `AskMemory` | Resposta, evidências, incertezas e referências temporais. |
| `SearchMemory` | Resultados filtráveis por entidade, data, tipo e status. |
| `GetEntity` | Visão atual, relações, histórico e fontes de uma entidade. |
| `GetTimeline` | Eventos e mudanças ordenados no tempo. |
| `CorrectMemory` | Solicitação auditável de correção ou exclusão. |
| `GetBriefing` | Contexto relevante para um dia ou reunião, quando habilitado. |
| `ApproveAction` | Aprovação explícita de uma ação que exige confirmação. |

Objetos da API devem trazer dados de domínio e evidência. A apresentação — linguagem, componentes, notificações e layout — pertence ao canal.

## 5. Princípios de interação

- Mostrar a origem de afirmações importantes e distinguir fato, inferência e sugestão.
- Explicar incerteza e ausência de dados em vez de inventar segurança.
- Priorizar a informação atual sem ocultar mudanças relevantes do histórico.
- Permitir que o usuário revise e corrija o sistema sem precisar entender sua arquitetura.
- Solicitar confirmação explícita antes de ações de alto risco.
- Evitar notificações proativas sem relevância, preferência e mecanismo simples de controle.

## 6. Canais e evolução

| Fase | Canais e capacidades |
| --- | --- |
| V1 | WhatsApp para consulta, busca, revisão e exploração básica; captura pelo iPhone. |
| V2 | Notificações e briefings diário/pré-reunião no WhatsApp. |
| V3 | Web, Telegram, e-mail e outros canais para interação e ações aprovadas. |

Adicionar um novo canal deve exigir apenas um adaptador de autenticação, apresentação e entrega. Regras de memória, respostas e autorização continuam em Agents.

## 7. Pré-requisitos do WhatsApp

Antes da integração da UI, é necessário ter uma conta e número habilitados para a plataforma comercial do WhatsApp, um aplicativo configurado para receber webhooks e credenciais guardadas fora do repositório. Também será necessário um fluxo inicial de vinculação entre o número de WhatsApp e a conta/workspace no Supabase.

Esses pré-requisitos não bloqueiam o desenvolvimento atual do backend: a integração começa somente depois que o fluxo local de memória e consulta estiver pronto.

## 8. Critérios de pronto

- O usuário consegue consultar memória e abrir as fontes que sustentam a resposta.
- É possível distinguir o estado atual de fatos antigos ou substituídos.
- Correções e exclusões passam pela API do backend e retornam confirmação compreensível.
- A UI não duplica lógica de memória, autorização ou consolidação.
- O webhook do WhatsApp é validado e um número só acessa o workspace a ele vinculado.
- A mesma capacidade pode ser apresentada por outro canal sem alterar o domínio.
