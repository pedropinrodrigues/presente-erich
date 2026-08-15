# Etapa 01 — Fundação do projeto

## Objetivo

Criar uma base local e repetível em Python/FastAPI para API e worker antes de introduzir regras de domínio. Ao final, qualquer pessoa do time deve conseguir instalar dependências, configurar variáveis de exemplo, executar testes e iniciar os processos sem conhecer detalhes internos do projeto.

## Pré-requisitos

- Acesso de escrita ao repositório.
- Runtime e gerenciador de dependências escolhidos pelo time.
- Python 3.12+, FastAPI, Pydantic, SQLAlchemy, Alembic, `uv`, `pytest` e `ruff` como stack definida.

Não é necessário acesso ao Supabase ou ao provedor de IA nesta etapa.

## Implementação, na ordem

1. Criar o projeto Python e congelar versões mínimas de runtime e dependências com `uv`.
2. Separar módulos de `api`, `worker`, `ingestion`, `memory`, `retrieval` e `model-gateway`, ainda que alguns estejam vazios.
3. Configurar lint, formatador, verificação de tipos quando aplicável e testes unitários.
4. Definir configuração tipada por ambiente e criar arquivo `.env.example` sem credenciais reais.
5. Criar comandos oficiais para iniciar API, iniciar worker, executar testes, lint e migrations.
6. Expor `GET /health` e, se houver dependências configuradas, `GET /ready`; ambos não retornam dados privados.
7. Documentar o bootstrap no README técnico do código quando ele existir.

## Entregáveis

- Estrutura de diretórios coerente com os módulos do MVP.
- Arquivo de configuração de exemplo e validação de variáveis obrigatórias.
- Scripts de desenvolvimento, lint, testes e migrations.
- Health check funcional.
- Pipeline local ou de CI que execute lint e testes.

## Testes obrigatórios

- Inicialização sem variável obrigatória falha com mensagem segura e clara.
- `GET /health` retorna sucesso sem autenticação e sem dados internos.
- Pelo menos um teste unitário é descoberto e executado pelo comando oficial.
- Lint e formatação passam em checkout limpo.

## Gate de pronto

- Clone limpo + instruções do repositório bastam para subir API e worker.
- O pipeline automatizado falha quando lint ou testes falham.
- Nenhuma credencial real está rastreada pelo Git.

## Não fazer nesta etapa

- Criar tabelas de domínio ou rotas privadas.
- Integrar Supabase Auth, SDK da OpenAI ou lógica de memória.
- Adicionar abstrações para futura arquitetura distribuída.
