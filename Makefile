.PHONY: help install dev test test-db-up test-db-down lint docker-up docker-down docker-build clean

help:
	@echo ""
	@echo "  make install       Install Python dependencies"
	@echo "  make dev           Run dev server with hot reload"
	@echo "  make test          Run test suite (needs Postgres — see test-db-up)"
	@echo "  make test-db-up    Start a throwaway Postgres container for testing"
	@echo "  make test-db-down  Stop the test Postgres container"
	@echo "  make lint          Lint with ruff"
	@echo "  make docker-up     Start Postgres + Redis + app via Docker Compose"
	@echo "  make docker-down   Stop Docker Compose services"
	@echo "  make docker-build  Rebuild Docker image"
	@echo "  make clean         Remove __pycache__ and .pytest_cache"
	@echo ""

install:
	pip install -r requirements.txt

dev:
	uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# Spin up a dedicated test-only Postgres container (separate from docker-compose)
test-db-up:
	docker run -d --name stockapp-test-db \
	  -e POSTGRES_DB=stockapp_test \
	  -e POSTGRES_USER=stockapp \
	  -e POSTGRES_PASSWORD=stockapp \
	  -p 5433:5432 \
	  postgres:16-alpine
	@echo "Waiting for Postgres to be ready..."
	@sleep 2

test-db-down:
	docker rm -f stockapp-test-db 2>/dev/null || true

test:
	DATABASE_URL=postgresql://stockapp:stockapp@localhost:5433/stockapp_test \
	REDIS_URL=redis://localhost:6379 \
	SECRET_KEY=test-secret-key-not-for-production \
	FINNHUB_API_KEY=dummy \
	ANTHROPIC_API_KEY=dummy \
	AI_PROVIDER=anthropic \
	ENVIRONMENT=testing \
	pytest tests/ -v --asyncio-mode=auto

lint:
	ruff check app/ tests/

docker-up:
	docker compose up --build

docker-down:
	docker compose down

docker-build:
	docker compose build

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete
