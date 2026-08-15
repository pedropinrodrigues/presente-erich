# Guia de Desenvolvimento — Agents & Backend MVP

## Propósito e limite

Este é o plano de execução do backend MVP. Ele cobre apenas API, worker, banco, memória e recuperação. Captura no iPhone e interfaces de usuário atuam somente como clientes dos contratos publicados.

Não iniciar uma etapa antes de passar no gate da anterior. Skills, automações, calendário, briefings, múltiplos agentes e microsserviços permanecem fora deste plano.

## Pré-requisitos compartilhados

| Acesso | Uso | Necessário a partir de |
| --- | --- | --- |
| Repositório e ambiente local | Código, testes e migrations. | Etapa 0 |
| Projeto Supabase com permissão administrativa | Auth, PostgreSQL, `pgvector` e migrations. | Etapa 1 |
| Credenciais do Supabase | Validação de JWT, banco e worker locais. | Etapa 1 |
| Chave da OpenAI | Extração estruturada e respostas pelo SDK Python. | Etapa 3 |

Segredos ficam em variáveis de ambiente ou cofre de segredos; nunca em código, logs, fixtures ou documentação.

## Etapas e dependências

```text
00. Preparação do usuário
   ↓
01 Fundação
   ↓
02 Identidade e persistência
   ↓
03 Ingestão idempotente
   ↓
04 Extração e memória
   ↓
05 Busca e respostas
   ↓
06 Correções, exclusão e qualidade
```

| Etapa | Resultado | Documento |
| --- | --- | --- |
| 00 | Contas, acessos e decisões externas que desbloqueiam o desenvolvimento. | [00-user-preparation.md](00-user-preparation.md) |
| 01 | Projeto local executável e verificável. | [01-foundation.md](01-foundation.md) |
| 02 | Dados isolados por usuário/workspace e schema inicial. | [02-identity-and-persistence.md](02-identity-and-persistence.md) |
| 03 | Transcrições persistidas e processáveis sem duplicação. | [03-idempotent-ingestion.md](03-idempotent-ingestion.md) |
| 04 | Memória estruturada, temporal e ligada às fontes. | [04-extraction-and-memory.md](04-extraction-and-memory.md) |
| 05 | Perguntas e buscas fundamentadas em evidências. | [05-search-and-answers.md](05-search-and-answers.md) |
| 06 | Correção, exclusão e qualidade aprovada para piloto. | [06-corrections-deletion-and-quality.md](06-corrections-deletion-and-quality.md) |

## Convenções para todas as etapas

- Entregar mudanças em PRs/commits pequenos, com testes relevantes.
- Criar migrations compatíveis com rollback ou com plano explícito de compatibilidade.
- Validar JWT e filtrar sempre por `workspace_id` nas rotas privadas.
- Correlacionar logs por `request_id` e `job_id`, sem registrar texto completo de transcrições.
- Atualizar contratos e documentação sempre que uma fronteira mudar.

## Definição global de pronto

O backend MVP está pronto somente quando as seis etapas passarem e o fluxo abaixo funcionar localmente com o Supabase hospedado:

```text
Transcript Event idempotente → fonte persistida → job processado
→ memória com evidência → pergunta fundamentada → correção ou exclusão auditável
```

O piloto resultante valida memória e consulta; ele não autoriza ações externas automatizadas.
