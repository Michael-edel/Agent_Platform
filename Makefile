# Makefile для Agent Platform / CyberPlat
# Работает на Linux/Mac и Windows (через Git Bash)

.PHONY: help install test lint check run worker docker-worker-logs observability-up observability-down grafana-open prometheus-open recurring-kaspi recurring-stripe clean

help: ## Показать справку по командам
	@echo "Доступные команды:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

install: ## Установить зависимости (production + dev)
	pip install -r requirements.txt
	pip install -r requirements-dev.txt

test: ## Запустить тесты
	python -m pytest -q

test-verbose: ## Запустить тесты с подробным выводом
	python -m pytest -v

lint: ## Проверить код через ruff
	ruff check .

check: lint test ## Запустить lint + test

run: ## Запустить FastAPI сервер
	uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

worker: ## Запустить Billing Worker локально
	@PYTHONPATH=. python -m cyberplat.billing.worker

docker-worker-logs: ## Tail logs billing worker (docker-compose)
	docker-compose logs -f worker

observability-up: ## Запустить Prometheus+Grafana (docker-compose profile observability)
	docker-compose --profile observability up -d --build

observability-down: ## Остановить Prometheus+Grafana
	docker-compose --profile observability down

grafana-open: ## Показать URL Grafana
	@echo "Grafana: http://localhost:3000 (admin/admin)"

prometheus-open: ## Показать URL Prometheus
	@echo "Prometheus: http://localhost:9090"

recurring-kaspi: ## Запустить Kaspi recurring billing вручную
	python scripts/run_kaspi_recurring.py

recurring-stripe: ## Запустить Stripe recurring billing вручную
	python scripts/run_stripe_recurring.py

clean: ## Очистить временные файлы и кэши
	find . -type d -name "__pycache__" -exec rm -r {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -r {} + 2>/dev/null || true
	find . -type d -name ".mypy_cache" -exec rm -r {} + 2>/dev/null || true
	find . -type f -name "*.db" -not -path "./.git/*" -delete 2>/dev/null || true

setup-env: ## Создать .env из шаблона (если не существует)
	@if [ ! -f .env ]; then \
		cp .env.example .env; \
		echo "✓ Создан .env из .env.example"; \
		echo "⚠️  Заполните значения в .env файле!"; \
	else \
		echo "⚠️  .env уже существует, пропуск"; \
	fi

# Database migration commands (PostgreSQL only)
db-current: ## Показать текущую версию миграции
	@python -m alembic current

db-history: ## Показать историю миграций
	@python -m alembic history

db-upgrade: ## Применить миграции (upgrade to head)
	@python -m alembic upgrade head

db-downgrade: ## Откатить последнюю миграцию
	@python -m alembic downgrade -1

db-check: ## Проверить, что revision == head (для CI/staging)
	@PYTHONPATH=. python -c "from alembic.config import Config; from alembic.script import ScriptDirectory; from alembic.runtime.migration import MigrationContext; from sqlalchemy import create_engine; from utils.db_url import get_sqlalchemy_database_url; db_url = get_sqlalchemy_database_url(); assert db_url, 'DATABASE_URL not set'; engine = create_engine(db_url); cfg = Config('alembic.ini'); script = ScriptDirectory.from_config(cfg); head_rev = script.get_current_head(); conn = engine.connect(); context = MigrationContext.configure(conn); current_rev = context.get_current_revision(); conn.close(); engine.dispose(); assert current_rev == head_rev, f'Schema version mismatch: current={current_rev}, expected={head_rev}'; print(f'✓ Schema is up-to-date (revision: {current_rev})')"

doctor: ## Запустить диагностику БД и конфигурации
	@PYTHONPATH=. python scripts/doctor.py
