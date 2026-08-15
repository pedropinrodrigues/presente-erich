# Etapa 00 — Preparação do usuário

## Objetivo

Deixar contas, acessos, decisões e dados de teste prontos antes do desenvolvimento. Conclua esta etapa uma vez; depois disso, as etapas 01 a 06 podem seguir sem depender de novas escolhas externas, salvo mudança intencional de escopo.

## O que você precisa preparar

### 1. Repositório e ambiente de trabalho

- Confirmar que o repositório atual é o local oficial do backend.
- Conceder permissão de escrita e de criação de commits/branches, se desejar que eu faça commits.
- A stack já está definida: Python 3.12+, `venv` + `pip`, FastAPI, Pydantic, SQLAlchemy, Alembic, `pytest` e `ruff`.
- A IA usa o SDK Python oficial da OpenAI. LangGraph ou LangChain não são necessários inicialmente e só serão avaliados se um workflow futuro justificar.

**Pronto quando:** essas escolhas são mantidas como padrão do MVP; não há decisão técnica pendente nesta seção.

### 2. Supabase

Criar uma organização e **um único projeto Supabase** exclusivo para este produto, de preferência em região compatível com os dados dos usuários. Ele hospeda PostgreSQL, `pgvector` e Supabase Auth; API e worker continuam locais.

Disponibilizar, por canal seguro ou diretamente em variáveis locais não versionadas:

```text
SUPABASE_URL
SUPABASE_ANON_KEY
SUPABASE_SERVICE_ROLE_KEY
DATABASE_URL ou credencial de conexão apropriada
```

Não envie chaves em chat nem as adicione ao Git. Você pode preenchê-las em `.env.local` (ignorado) ou em um cofre/integração de segredos que eu possa usar.

**Pronto quando:** consigo conectar no banco hospedado, aplicar migrations e validar JWT localmente.

### 3. OpenAI e faturamento

Criar ou selecionar a conta OpenAI do MVP, com faturamento habilitado e limite de gasto configurado.

Disponibilizar uma chave de API para uso local, com limite de gasto configurado. Registrar o nome do modelo inicialmente aprovado para extração e resposta. O backend usa o SDK Python da OpenAI, Responses API e `store=false`.

```text
LLM_API_KEY
LLM_MODEL_EXTRACTION
LLM_MODEL_ANSWERING
```

**Pronto quando:** há uma chave válida, cobrança habilitada, limite de gasto definido e modelo inicial escolhido. Sem isso, as etapas 01–03 podem avançar, mas a etapa 04 ficará bloqueada.

### 4. Decisões de dados e piloto

Decisões aprovadas:

- O MVP será de uso pessoal, com um único `owner` por workspace.
- Fontes e memórias são mantidas até exclusão do usuário.
- Backups podem reter dados por até 30 dias, mas registros excluídos não podem voltar à aplicação.
- O piloto não usará transcrições de terceiros sem consentimento.
- A primeira entrega foca em criar e manter corretamente a wiki/memória. Agentes que respondem mensagens, enviam e-mails ou executam automações ficam para depois do MVP.

Separar também um conjunto de ao menos 30 transcrições **sintéticas**, sem segredos desnecessários, para avaliação. Elas devem conter decisões, compromissos, pessoas com aliases, mudanças de prazo e perguntas esperadas.

**Pronto quando:** as decisões são aprovadas e o dataset de avaliação está disponível antes da etapa 06.

### 5. Acesso de teste e operação manual

- Criar ao menos uma conta de teste no Supabase Auth.
- Definir quem aprova a qualidade do piloto.
- Manter um canal para decisões rápidas de produto, como nome de entidade ambíguo ou regra de correção.

**Pronto quando:** existe usuário de teste, responsável pelo piloto e caminho de comunicação para decisões de produto.

## Ordem prática para você executar

1. Criar o projeto Supabase e configurar credenciais locais seguras.
2. Escolher/autorizar a conta OpenAI, habilitar cobrança e configurar a chave local.
3. Aprovar política de dados/piloto e criar usuário de teste.
4. Preparar o dataset de avaliação antes da etapa 06; ele não bloqueia a fundação.

## Checklist de liberação para iniciar a Etapa 01

- [ ] Repositório disponível para desenvolvimento.
- [ ] Projeto Supabase criado, com Auth e `pgvector` disponíveis.
- [ ] Credenciais locais disponíveis apenas por mecanismo seguro.
- [x] Decisões de dados e piloto aprovadas.

Com essa checklist concluída, posso executar as etapas 01 a 03 sem nova dependência externa. Para as etapas 04 e 05, preciso adicionalmente da chave da OpenAI. Para a etapa 06, preciso apenas do dataset de avaliação.

## O que não é necessário agora

- App do iPhone, Apple Developer Program ou integração de transcrição.
- Deploy de API/worker, domínio próprio e e-mail transacional.

O WhatsApp é a interface definida para o produto, mas a conta comercial, o número, os webhooks e as credenciais do canal só serão necessários quando o backend MVP local estiver concluído e a etapa de UI começar.
- CRM, calendário, WhatsApp, Telegram ou Skills.
- Arquitetura de microsserviços, Redis, Kafka ou banco de grafo.
