# Desenvolvimento local e banco hospedado

## Escopo atual

O MVP roda localmente: FastAPI e worker são processos iniciados na máquina de desenvolvimento. O banco e a autenticação ficam em um único projeto Supabase hospedado. Não há deploy da aplicação, staging, domínio, CI/CD remoto ou preocupação de escala nesta fase.

```text
Máquina local
  ├─ FastAPI
  └─ Worker Python
          │
          ▼
Projeto Supabase (PostgreSQL + Auth) ← SDK OpenAI
```

Deploy da API e do worker será tratado somente depois que o fluxo do MVP estiver útil localmente.

## Configuração local

Usar Python 3.12+, `uv` para dependências e ambientes virtuais, `pytest` para testes, `ruff` para lint/formatação, Alembic para migrations e SQLAlchemy para acesso ao banco. As variáveis mínimas são:

```text
SUPABASE_URL
SUPABASE_ANON_KEY
SUPABASE_SERVICE_ROLE_KEY
DATABASE_URL
OPENAI_API_KEY
OPENAI_MODEL_EXTRACTION
OPENAI_MODEL_ANSWERING
```

Essas variáveis ficam em `.env.local`, ignorado pelo Git. O repositório contém somente `.env.example` com nomes de variáveis, nunca valores.

## Banco e migrations

O Supabase é a única dependência hospedada do MVP. Migrations Alembic são aplicadas manualmente, em ordem, a partir da máquina local. Antes de cada migration nova, verificar em uma base local/descartável ou usar migration reversível; a aplicação não faz migrations automaticamente ao iniciar.

O banco precisa ter `pgvector` habilitado antes da busca vetorial. A busca textual e os filtros estruturados continuam funcionando mesmo que embeddings ainda não estejam configurados.

## Segurança e logs

- API valida JWT do Supabase em toda rota privada.
- Worker usa credencial de serviço apenas no ambiente local e nunca a expõe a clientes.
- Segredos, transcrições completas e respostas completas do modelo não entram nos logs.
- Chamadas à OpenAI usam `store=false` e registram apenas identificador, versão de prompt/modelo, duração, tokens e erro normalizado.

## Testes locais

O teste de integração usa um banco de teste ou um schema descartável no projeto Supabase, nunca dados reais do usuário. Os testes cobrem migrations, isolamento por workspace, ingestão, worker, extração e busca de ponta a ponta. Não é necessário staging para considerar a etapa pronta.
