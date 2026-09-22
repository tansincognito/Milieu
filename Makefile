.PHONY: up down migrate check-fast check

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
