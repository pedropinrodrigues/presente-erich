# Context Pack — Assistente Pessoal com Memória de Longo Prazo

## 1. Source manifest

| Fonte | Papel |
| --- | --- |
| `CONTEXT-PACK.md` | Fonte principal de visão de produto, escopo, fronteiras e delegação. |
| `CAPTURE-INGESTION/CAPTURE-INGESTION.md` | Especificação da captura no dispositivo e entrega do `Transcript Event`. |
| `AGENTS-BACKEND/AGENTS-BACKEND.md` | Guia do MVP do backend; detalhes ficam em `AGENTS-BACKEND/docs/`. |
| `USER-INTERFACE/USER-INTERFACE.md` | Especificação dos clientes e da apresentação ao usuário. |
| `IDEA.md` | Resumo histórico de alto nível; não substitui os documentos acima. |

## 2. Source priority

Em caso de conflito, aplicar esta ordem:

1. Requisitos explícitos do solicitante.
2. Este documento, para escopo, decisões compartilhadas e restrições consolidadas.
3. O documento específico da área afetada, para decisões de implementação locais.
4. `IDEA.md`, somente como resumo e contexto histórico.
5. Suposições de implementação, que devem ser registradas e mantidas reversíveis.

## 3. Objective

Construir um assistente pessoal que converta informações cotidianas em uma memória de longo prazo útil, confiável e acionável. O primeiro caso de uso é registrar pensamentos, conversas e reuniões pela voz, com o mínimo de esforço para o usuário.

O produto não é um repositório de transcrições nem apenas um chatbot. Ele deve preservar a fonte original, identificar o que importa, acompanhar mudanças no tempo e permitir que o usuário recupere contexto, decisões, pendências e relações entre pessoas, empresas e projetos. O WhatsApp é o canal inicial de interação do usuário.

### Hipótese do MVP

Depois de acumular capturas reais, o usuário consegue recuperar decisões, compromissos e contexto com menos esforço e mais confiança do que procurando manualmente nas fontes originais.

### Resultado do fluxo principal

```text
Usuário fala
  → iPhone gera texto localmente
  → backend recebe e preserva a fonte
  → sistema extrai e consolida memória
  → usuário consulta e recebe resposta com evidências
```

O backend deve distinguir fatos explícitos, inferências, sugestões e incertezas. A Wiki e as interfaces são formas de explorar a memória; a memória estruturada, com suas fontes, é o ativo central.

### Organização da documentação e delegação

Este arquivo introduz o produto, define as fronteiras e distribui o trabalho entre as áreas. As decisões específicas de cada área pertencem ao respectivo documento:

| Área | Documento | Responsabilidade de implementação |
| --- | --- | --- |
| Capture & Ingestion | `CAPTURE-INGESTION/CAPTURE-INGESTION.md` | Capturar, transcrever no dispositivo, manter fila local e entregar texto com segurança. |
| Agents & Backend | `AGENTS-BACKEND/AGENTS-BACKEND.md` | Persistir fontes, construir memória, recuperar contexto e responder; detalhes em `docs/`. |
| User Interface | `USER-INTERFACE/USER-INTERFACE.md` | Integrar o WhatsApp e apresentar memória e fluxos sem conter regras centrais de domínio. |

Cada área pode evoluir de forma independente desde que preserve os contratos publicados. Alterações que cruzem fronteiras — como o formato do `Transcript Event`, autenticação, confirmação de entrega ou objetos retornados pela API — devem ser alinhadas entre os documentos afetados antes da implementação.

### Evolução do produto

```text
Captura local → Memória / Segundo cérebro → Assistente executivo → Agente pessoal
```

O MVP concentra-se em captura, memória e consulta. Briefings, calendário, integrações, mensagens e automações ficam para fases posteriores, após qualidade comprovada de memória e recuperação.

## 4. Acceptance criteria

- O iPhone captura áudio temporariamente e produz a transcrição no próprio dispositivo.
- O backend recebe somente texto e metadados por meio de um `Transcript Event`; áudio não é enviado nem persistido no fluxo normal.
- Cada evento possui `capture_id` único e o processamento é idempotente.
- O dispositivo mantém fila local de transcrições não confirmadas e tenta reenviar usando o mesmo `capture_id`.
- O backend confirma o recebimento apenas após persistir o evento de forma durável; a extração pode ocorrer de modo assíncrono.
- O áudio local é excluído somente após haver transcrição válida e confirmação durável de entrega.
- A ingestão extrai entidades, fatos, decisões, compromissos, pendências, problemas, ideias e oportunidades a partir do texto.
- Toda memória relevante mantém evidência: fonte, trecho quando disponível, data, confiança, validade e status.
- O sistema reconhece atualizações, duplicatas, conflitos e referências alternativas à mesma entidade.
- Consultas usam informação recente e confiável, com histórico e fontes recuperáveis.
- A Wiki é uma projeção reconstruível da memória estruturada, e não a fonte de verdade.
- A camada de inteligência é independente da interface e da tecnologia de captura.

## 5. Constraints

- Arquitetura com três camadas: **Capture & Ingestion**, **Agents** e **User Interface**.
- `Capture & Ingestion` apenas captura, transcreve, valida minimamente e entrega texto; não interpreta conteúdo nem atualiza memória.
- `Agents` recebe texto e metadados, nunca depende do arquivo de áudio ou do método de transcrição.
- No MVP, `Agents` é um monólito modular com workflows especializados, não múltiplos agentes autônomos.
- Modelos de IA podem extrair, classificar e sugerir operações, mas não podem persistir dados nem executar ações diretamente.
- Serviços de domínio validam schema, evidência, temporalidade, autorização e idempotência antes de mudanças ou ações.
- A busca deve ser híbrida: vetorial, textual, estruturada, por relações, datas, tipo, importância e recência.
- A implementação inicial não exige app próprio, mensageria, CRM, automação complexa, múltiplos agentes ou muitas Skills.

## 6. Security & client

- Áudio e transcrições temporárias devem usar as proteções disponíveis no iPhone e não podem ser expostos a outros apps sem autorização.
- O áudio existe apenas localmente e pelo tempo necessário para obter uma transcrição válida e confirmar sua entrega.
- Se a entrega falhar, a transcrição permanece em fila local; retries usam o texto já salvo, sem nova transcrição.
- O usuário pode consultar, corrigir e excluir memórias; também controla ações externas e níveis de autonomia.
- Ações de baixo risco podem ser automáticas; ações de risco médio devem permitir revisão; ações de alto risco exigem autorização explícita inicialmente.
- Informações factuais devem preservar proveniência para explicar por que uma resposta foi dada.

## 7. Open questions

- Qual interface será priorizada após o MVP: web, app, WhatsApp, Telegram ou híbrida?
- Qual mecanismo nativo do iPhone será usado para a captura inicial: Atalho, widget, Action Button ou combinação?
- Quais schemas e limites definem uma memória válida, conflito, atualização e deduplicação?
- Como será feita a confirmação e correção humana de fatos de baixa confiança?
- Quais métricas e limites de qualidade habilitam a evolução para briefings e proatividade?
- Quais integrações externas serão incluídas primeiro na V2 e V3?

## 8. Excluded sources

- Arquivos de áudio não são fonte de verdade do backend nem material de reprocessamento.
- A implementação visual de uma UI específica não orienta a arquitetura do núcleo.
- Tecnologias particulares de transcrição não devem influenciar a lógica central de `Agents`.
- Conteúdo curto, incompleto ou ambíguo não deve ser descartado automaticamente por relevância aparente; apenas falhas inequívocas são descartadas no dispositivo.

## 9. Handoff notes

### Contrato de entrada

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

### Fluxo do MVP

```text
Captura local → Transcrição no dispositivo → Validação básica
→ Persistência local do texto → Envio idempotente → Confirmação durável
→ Exclusão do áudio → Extração → Memory Manager → Consulta
```

### Módulos de Agents

- **Knowledge Ingestion:** transforma texto não estruturado em informações estruturadas.
- **Memory / Memory Manager:** mantém memória raw, semântica, episódica, operacional e de interação; resolve entidades, versões e conflitos.
- **Retrieval / Reasoning:** recupera contexto e fontes para responder com precisão temporal.
- **Skills / Actions:** integra capacidades externas com autorização proporcional ao risco.
- **Scheduler / Proactivity:** produz briefings e alertas contextuais quando a qualidade da memória permitir.

### Entregas por fase

| Fase | Foco |
| --- | --- |
| V1 — Segundo Cérebro | Captura, transcrição local, entrega idempotente, extração, memória, Wiki inicial, busca e chat. |
| V2 — Assistente Executivo | Calendário, briefings, pendências, alertas e acompanhamento. |
| V3 — Agente Pessoal | E-mail, mensagens, documentos, CRM, Skills e automação autorizada. |
