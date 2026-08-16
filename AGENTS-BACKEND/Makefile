.PHONY: install api worker test lint format check migrate evaluate evaluate-live

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
