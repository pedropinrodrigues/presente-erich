# Ideia do Produto — resumo

Este repositório descreve um assistente pessoal com memória de longo prazo. Ele começa registrando falas no iPhone, transforma texto em memória estruturada e permite recuperar decisões, pendências, relações e contexto com suas fontes.

O áudio é temporário e local ao dispositivo. O backend recebe texto e metadados, preserva a transcrição como evidência e constrói uma memória temporal, corrigível e consultável.

## Arquitetura

```text
Capture & Ingestion → Agents & Backend → User Interface
```

- **Capture & Ingestion** captura e transcreve; não interpreta nem armazena áudio no backend.
- **Agents & Backend** interpreta, memoriza, recupera contexto e controla ações autorizadas.
- **User Interface** usa inicialmente o WhatsApp para consultar, revisar e explorar a memória; não é fonte de verdade.

## Documentação vigente

- `CONTEXT-PACK.md`: ponto de partida, objetivo, fronteiras, critérios e delegação entre áreas.
- `CAPTURE-INGESTION/CAPTURE-INGESTION.md`: especificação da captura local e entrega confiável de transcrições.
- `AGENTS-BACKEND/AGENTS-BACKEND.md`: guia do MVP do núcleo de memória, busca e backend.
- `USER-INTERFACE/USER-INTERFACE.md`: especificação dos clientes e da experiência de uso.

O MVP deve resolver muito bem: capturar → transcrever localmente → enviar texto → memorizar → consultar com fontes. O restante evolui a partir dessa base.
