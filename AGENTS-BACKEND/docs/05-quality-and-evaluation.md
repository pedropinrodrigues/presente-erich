# Qualidade e avaliação do MVP

## Objetivo

Garantir que o sistema não pareça útil apenas porque produz texto convincente. A avaliação mede extração, recuperação e sustentação das respostas em fontes reais ou sintéticas revisadas.

## Dataset inicial

Manter um conjunto versionado com pelo menos 30 transcrições curtas e médias, em português, contendo: pessoas com aliases, decisões, compromissos, datas, atualizações de fatos, duplicatas e informação ambígua. Cada item possui gabarito de entidades, fatos, evidências e perguntas esperadas.

O dataset não pode conter transcrições reais sem consentimento explícito e deve ser executado antes de mudanças de prompt, modelo ou schema.

## Métricas e gates

| Métrica | Como medir | Gate para o piloto |
| --- | --- | --- |
| Validade estrutural | Saídas de extração que passam no JSON Schema. | ≥ 98% |
| Precisão de extração | Candidatos corretos entre os candidatos persistidos. | ≥ 90% |
| Cobertura de extração | Fatos e compromissos esperados recuperados. | ≥ 80% |
| Resposta fundamentada | Afirmações relevantes sustentadas por evidência apresentada. | ≥ 95% |
| Recuperação útil | Perguntas com resposta correta ou incerteza explícita. | ≥ 85% |
| Duplicação indevida | Memórias repetidas entre execuções/retries. | ≤ 2% |

Os resultados devem ser segmentados por tipo de informação e por versão de modelo/prompt. Se um gate falhar, a mudança não segue para o piloto sem revisão explícita.

## Avaliação humana

Uma pessoa revisa amostra de respostas e classifica: correta, parcialmente correta, incorreta, sem evidência ou excessivamente confiante. Correções feitas no produto também alimentam uma fila de exemplos para ampliar o dataset, após anonimização ou autorização.

## Casos obrigatórios

- o mesmo `capture_id` enviado duas vezes;
- mudança de prazo que substitui uma versão anterior;
- “Carlos” e “Carlos Silva” com evidência suficiente e insuficiente para fusão;
- pergunta sem evidência, que deve responder incerteza;
- exclusão de uma fonte seguida de busca e pergunta;
- extração que retorna JSON inválido ou baixa confiança.
