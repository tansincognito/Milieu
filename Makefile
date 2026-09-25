.PHONY: up down migrate check-fast check eval

up:
	docker compose up -d

down:
	docker compose down

migrate:
	cd api && uv run alembic upgrade head

check-fast:
	cd api && uv run ruff check app tests
	cd api && uv run mypy app
	cd api && uv run pytest -m "not integration"

check: up migrate
	cd api && uv run ruff check app tests
	cd api && uv run mypy app
	cd api && uv run pytest

# Eval harness (§19): runs the real pipeline against /mock-data through a live LLM call.
# Kept separate from `check`/`check-fast`, which must never hit a live model.
eval: up migrate
	cd api && uv run python -m evals.runner
