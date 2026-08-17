# Dataset sintético inicial

`synthetic-transcripts.jsonl` contém 30 transcrições fictícias em português. Cada linha inclui um `capture_id`, o texto de origem e um gabarito inicial de entidades, fatos, compromissos, versões substituídas, incertezas e perguntas.

O conjunto não contém pessoas, empresas ou informações reais. Ele cobre decisões, alterações de prazo, aliases com evidência suficiente e insuficiente, pendências abertas e concluídas, fatos incertos e perguntas que devem resultar em incerteza.

Os efeitos de domínio — reenvio de `capture_id`, baixa confiança, retries, correções e exclusões —
também são cobertos pela suíte de testes.

`synthetic-agent-turns.jsonl` contém intenções conversacionais para leitura, escrita, confirmação,
cancelamento, prompt injection e pedidos fora do catálogo. Execute:

```bash
make evaluate-conversation
```

Essa avaliação chama o modelo real, mas devolve resultados simulados às function tools e nunca toca
nos dados de domínio. Ela mede seleção da primeira tool, validade dos argumentos, feedback final e
uso de tools proibidas. O relatório fica em `conversation-report.json`; use
`python scripts/evaluate_conversation.py --enforce-gates` para fazer os limites falharem o processo.

O relatório de 2026-08-16 com `gpt-5.6-luna` passou os 12 casos: 100% de seleção, argumentos e
feedback, sem uso de tool proibida. Esse resultado é um gate sintético, não substitui o piloto com
mensagens reais e revisão dos efeitos no domínio.
