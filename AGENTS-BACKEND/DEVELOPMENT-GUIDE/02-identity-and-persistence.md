# Etapa 02 — Identidade e persistência

## Objetivo

Estabelecer a fronteira de segurança e o schema mínimo do MVP. Todo dado deve pertencer a um workspace antes de qualquer caso de uso de transcrição ou memória existir.

## Pré-requisitos e acessos

- Etapa 01 aprovada.
- Projeto Supabase criado, com acesso administrativo ao banco e ao Supabase Auth.
- Credenciais locais do projeto; a credencial de serviço fica restrita ao worker/backend.

## Implementação, na ordem

1. Configurar validação de JWT emitido pelo Supabase Auth.
2. Implementar middleware que resolve `user_id` e workspace pessoal do usuário autenticado.
3. Criar migrations para workspaces e propriedade do `owner`.
4. Criar tabelas mínimas: `sources`, `jobs`, `entities`, `facts`, `commitments`, `evidence` e `audit_events`.
5. Adicionar `workspace_id`, chaves estrangeiras, índices de busca inicial e constraints de unicidade necessárias.
6. Implementar repositórios que recebem o contexto de workspace explicitamente, sem fallback global.
7. Adicionar política de acesso no banco quando aplicável e conferir que a API a respeita.

## Entregáveis

- Migrations versionadas, revisadas e aplicáveis em banco vazio.
- Middleware de autenticação e resolução de workspace.
- Modelo de estados de `Source`, `Job`, `Fact` e `Commitment` representado no domínio.
- Repositórios com escopo obrigatório por workspace.

## Testes obrigatórios

- JWT válido cria ou recupera o workspace pessoal correto.
- JWT ausente ou inválido retorna `401`.
- Usuário A não lê, altera ou exclui dados do usuário B, inclusive por ID conhecido.
- Migrations sobem em banco vazio e uma segunda execução é segura.
- Constraints impedem registros de domínio sem `workspace_id`.

## Gate de pronto

- Rotas privadas exigem identidade válida.
- Todo registro persistido do MVP possui workspace e auditoria possível.
- O isolamento é comprovado por testes automatizados, não apenas por convenção de código.

## Não fazer nesta etapa

- Liberar colaboração entre usuários ou papéis adicionais.
- Armazenar transcrições, chamar modelos ou criar endpoints de busca.
