.PHONY: dev worker test lint format typecheck migrate seed docker-up docker-down install install-dev

dev:
	uvicorn src.server:app --reload --port 3001

worker:
	arq src.queue.worker.WorkerSettings

test:
	pytest tests/ -v

test-cov:
	pytest tests/ --cov=src --cov-report=term-missing

lint:
	ruff check src/ tests/

format:
	ruff format src/ tests/

typecheck:
	mypy src/

migrate:
	python -m src.db.migrate

seed:
	python -m src.db.seed

docker-up:
	docker-compose up -d

docker-down:
	docker-compose down

install:
	pip install -e .

install-dev:
	pip install -e ".[dev]"
