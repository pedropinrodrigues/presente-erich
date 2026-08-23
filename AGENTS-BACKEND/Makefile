.PHONY: install api worker test lint format check migrate evaluate evaluate-live evaluate-conversation test-edge test-telegram-edge test-composio-edge deploy-whatsapp-edge deploy-telegram-edge deploy-composio-edge

SUPABASE_PROJECT_REF ?= onuxlluzwlnkhbsfiind

install:
	.venv/bin/pip install -r requirements-dev.txt

api:
	.venv/bin/uvicorn agents_backend.api.main:app --reload

worker:
	.venv/bin/python -m agents_backend.worker.main

test:
	.venv/bin/pytest

lint:
	.venv/bin/ruff check src tests scripts

format:
	.venv/bin/ruff format src tests scripts

check: lint test

migrate:
	.venv/bin/alembic upgrade head

evaluate:
	.venv/bin/python -m agents_backend.evaluation.runner

evaluate-live:
	.venv/bin/python scripts/evaluate_live.py

evaluate-conversation:
	.venv/bin/python scripts/evaluate_conversation.py

test-edge:
	npx --yes deno@latest test supabase/functions/whatsapp-webhook/core_test.ts

test-telegram-edge:
	npx --yes deno@latest test supabase/functions/telegram-webhook/core_test.ts

test-composio-edge:
	npx --yes deno@latest test supabase/functions/composio-callback/core_test.ts

deploy-whatsapp-edge:
	npx --yes supabase@latest functions deploy whatsapp-webhook --project-ref $(SUPABASE_PROJECT_REF) --use-api --no-verify-jwt

deploy-telegram-edge:
	npx --yes supabase@latest functions deploy telegram-webhook --project-ref $(SUPABASE_PROJECT_REF) --use-api --no-verify-jwt

deploy-composio-edge:
	npx --yes supabase@latest functions deploy composio-callback --project-ref $(SUPABASE_PROJECT_REF) --use-api --no-verify-jwt
