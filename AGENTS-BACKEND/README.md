# Agents & Backend MVP

Backend local em FastAPI que transforma transcrições em memória pesquisável e responde com evidências. Supabase hospeda Auth e PostgreSQL; API e worker executam localmente.

## Preparação

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env.local
```

Preencha `.env.local` sem versionar segredos. Em seguida:

```bash
make migrate
make api
```

Em outro terminal, com o ambiente ativado:

```bash
make worker
```

## Qualidade

```bash
make check
make evaluate
```

Para executar os gates reais contra Supabase e OpenAI:

```bash
python scripts/evaluate_live.py
```

É possível comparar modelos sem alterar `.env.local`:

```bash
python scripts/evaluate_live.py \
  --extraction-model gpt-5.6-luna \
  --answering-model gpt-5.6-luna \
  --case-ids syn-001,syn-002,syn-004,syn-009,syn-012,syn-013,syn-017,syn-018,syn-023,syn-024,syn-030
```

Uma execução com `--case-limit` ou `--case-ids` é somente benchmark e nunca aprova o piloto.
Remova a opção para executar os 30 casos. O relatório fica em `evaluation/live-report.json`.
Se uma execução válida já gerou o cache
`evaluation/live-extractions.json`, use `--reuse-extractions` para comparar somente o modelo de
resposta sem pagar novamente pelas extrações. O cache só é reutilizado quando modelo, prompt e
schema coincidem. O relatório também registra tokens, latência acumulada e custo estimado de
extração/resposta; embeddings são identificados separadamente como não incluídos nessa estimativa.

A API publica documentação em `/docs`, health em `/health` e readiness em `/ready`.
