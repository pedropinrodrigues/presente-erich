# Dataset sintético inicial

`synthetic-transcripts.jsonl` contém 30 transcrições fictícias em português. Cada linha inclui um `capture_id`, o texto de origem e um gabarito inicial de entidades, fatos, compromissos, versões substituídas, incertezas e perguntas.

O conjunto não contém pessoas, empresas ou informações reais. Ele cobre decisões, alterações de prazo, aliases com evidência suficiente e insuficiente, pendências abertas e concluídas, fatos incertos e perguntas que devem resultar em incerteza.

Os cenários que requerem efeitos de domínio — reenvio de `capture_id`, JSON inválido, baixa confiança e exclusão seguida de busca — serão adicionados aos testes de integração quando os contratos da API e do worker existirem.
