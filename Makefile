.PHONY: install lint typecheck test test-postgres iac-compatibility db-up db-down migrate api worker mcp demo build-images

install:
	python3 -m pip install -e '.[dev]'

lint:
	ruff check .
	ruff format --check .

typecheck:
	mypy src

test:
	pytest -m 'not postgres'

test-postgres:
	CONTROL_PLANE_TEST_DATABASE_URL=$${CONTROL_PLANE_DATABASE_URL} pytest -m postgres

iac-compatibility:
	pytest -q tests/test_iac_compatibility_live.py

db-up:
	docker compose up -d postgres

db-down:
	docker compose down

migrate:
	alembic upgrade head

api:
	control-plane-api

worker:
	control-plane-worker

mcp:
	control-plane-mcp

demo:
	control-plane demo

build-images:
	docker build --target api -t control-plane-api:local .
	docker build --target worker -t control-plane-worker:local .
