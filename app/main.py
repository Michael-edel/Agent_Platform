"""FastAPI приложение для обработки документов."""

import uuid
import logging
import os
import json
import threading
from pathlib import Path
from typing import Optional, Dict, Any, List
from datetime import datetime

from fastapi import FastAPI, UploadFile, File, HTTPException, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import sys
from pathlib import Path as PathLib

# Добавляем корневую директорию в путь для импортов
root_dir = PathLib(__file__).parent.parent
sys.path.insert(0, str(root_dir))

from utils.config import load_settings, Settings
from utils.db_url import normalize_sqlalchemy_database_url, get_original_database_url

# Импорты для observability
from cyberplat.observability.logging import setup_structured_logging
from cyberplat.observability.request_id import RequestIDMiddleware
from cyberplat.observability.metrics import (
    setup_metrics,
    metrics_endpoint,
    record_http_request,
    record_webhook_event,
    record_recurring_run,
)
from storage.database import Database
from storage.job_queue import JobQueue, JobStatus
from core.worker import DocumentWorker

# Импорты для платформы агентов
from cyberplat.artifact_service import ArtifactService
from cyberplat.event_service import EventService, TenantValidationError
from cyberplat.storage_service import StorageService
from cyberplat.agent_registry import AgentRegistry
from cyberplat.base_agent import AgentContext
from cyberplat.agents.doc_agent import DocAgent
from cyberplat.agents.payment_agent import PaymentAgent

# Импорты для S3 экспорта
from cyberplat.s3_exporter import S3Exporter
from cyberplat.s3_export_subscriber import S3ExportSubscriber

# Импорты для billing
from cyberplat.billing_service import BillingService
from cyberplat.billing_subscriber import BillingSubscriber
from cyberplat.billing_entitlements import EntitlementService
from cyberplat.stripe_webhook_handler import StripeWebhookHandler
from cyberplat.kaspi_webhook_handler import KaspiWebhookHandler
from cyberplat.kaspi_client import create_checkout_session as kaspi_create_checkout, charge_token as kaspi_charge_token

# Product/UI layer импорты перенесены в app/api/product.py

# Настройка структурированного логирования
log_level = os.getenv("LOG_LEVEL", "INFO").strip()
log_format = os.getenv("LOG_FORMAT", "json").strip()
setup_structured_logging(level=log_level, format_type=log_format)
logger = logging.getLogger(__name__)

# Инициализация приложения
app = FastAPI(
    title="Document Processing API",
    description="API для обработки PDF счетов и других документов",
    version="1.0.0"
)

# Exception handler for plan limit exceeded
from app.billing.limits import PlanLimitExceededError

@app.exception_handler(PlanLimitExceededError)
async def plan_limit_exceeded_handler(request: Request, exc: PlanLimitExceededError):
    """Return 429 with structured JSON for plan limit exceeded."""
    logger.warning(f"Plan limit exceeded: tenant={exc.tenant_id}, metric={exc.metric}")
    return JSONResponse(
        status_code=429,
        content={
            "error": "plan_limit_exceeded",
            "metric": exc.metric,
            "limit": exc.limit,
            "used": exc.used,
            "tenant_id": exc.tenant_id,
        },
    )

# Настройка admin panel (SQLAdmin)
from app.admin.setup import setup_admin
setup_admin(app)

# Настройка метрик
metrics_enabled = os.getenv("METRICS_ENABLED", "1").strip() == "1"
setup_metrics(enabled=metrics_enabled)

# Подключение middleware для request_id (должен быть первым)
app.add_middleware(RequestIDMiddleware)

# Middleware для метрик HTTP запросов (если включены)
if metrics_enabled:
    from starlette.middleware.base import BaseHTTPMiddleware
    from time import time
    
    class MetricsMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            start_time = time()
            method = request.method
            path = request.url.path
            
            try:
                response = await call_next(request)
                status_code = response.status_code
                return response
            except Exception as e:
                status_code = 500
                raise
            finally:
                duration = time() - start_time
                # Исключаем /metrics и /health из метрик (чтобы не зашумлять)
                if path not in ("/metrics", "/health", "/ready"):
                    record_http_request(method, path, status_code, duration)
    
    app.add_middleware(MetricsMiddleware)

# Подключение API роутеров на import-time (для тестов с TestClient)
# Роутеры подключаются здесь, чтобы быть доступными без startup_event
from app.api.cases import router as cases_router
from app.api.payments import router as payments_router
app.include_router(cases_router, prefix="/api/v1", tags=["cases"])
app.include_router(payments_router, prefix="/api/v1", tags=["payments"])

# Глобальные объекты (инициализируются при старте)
settings: Optional[Settings] = None
db: Optional[Database] = None
queue: Optional[JobQueue] = None
worker: Optional[DocumentWorker] = None
stop_event: Optional[threading.Event] = None

# Платформа агентов
artifact_service: Optional[ArtifactService] = None
event_service: Optional[EventService] = None
storage_service: Optional[StorageService] = None
agent_registry: Optional[AgentRegistry] = None

# S3 экспорт
s3_exporter: Optional[S3Exporter] = None

# Billing
billing_service: Optional[BillingService] = None


@app.on_event("startup")
async def startup_event():
    """Инициализация при старте приложения."""
    global settings, db, queue, worker, stop_event
    global artifact_service, event_service, storage_service, agent_registry
    global s3_exporter
    
    logger.info("Инициализация приложения...")
    settings = load_settings()
    
    # Инициализация базы данных
    db = Database(settings.db_path)
    
    # Инициализация очереди задач
    queue = JobQueue()
    
    # Запуск воркера
    openai_api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not openai_api_key:
        logger.warning("OPENAI_API_KEY is empty; DocumentWorker will not be started")
        stop_event = None
        worker = None
    else:
        stop_event = threading.Event()
        worker = DocumentWorker(queue, stop_event)
        worker.start()
    
    # Инициализация платформы агентов
    # Создаем сервисы с циклической зависимостью (event_service нужен artifact_service, и наоборот)
    event_service = EventService()
    artifact_service = ArtifactService(event_service=event_service)
    # Обновляем event_service, чтобы он мог получать tenant_id из артефактов
    event_service.artifact_service = artifact_service
    storage_service = StorageService()
    agent_registry = AgentRegistry()
    
    # Сохраняем сервисы в app.state для доступа из endpoints
    app.state.artifact_service = artifact_service
    app.state.event_service = event_service
    app.state.storage_service = storage_service
    app.state.agent_registry = agent_registry
    
    # Регистрация doc_agent
    doc_agent = DocAgent(
        artifact_service=artifact_service,
        event_service=event_service,
        storage_service=storage_service
    )
    agent_registry.register(doc_agent)
    
    # Регистрация payment_agent
    payment_agent = PaymentAgent(
        artifact_service=artifact_service,
        event_service=event_service
    )
    agent_registry.register(payment_agent)
    
    # Инициализация S3 экспорта (опционально)
    s3_export_enabled = os.getenv("S3_EXPORT_ENABLED", "0").strip() == "1"
    if s3_export_enabled:
        try:
            s3_endpoint_url = os.getenv("S3_ENDPOINT_URL", "").strip() or None
            s3_access_key = os.getenv("S3_ACCESS_KEY", "").strip()
            s3_secret_key = os.getenv("S3_SECRET_KEY", "").strip()
            s3_bucket = os.getenv("S3_BUCKET", "").strip()
            s3_region = os.getenv("S3_REGION", "us-east-1").strip()
            s3_prefix = os.getenv("S3_PREFIX", "").strip()
            
            if not s3_access_key or not s3_secret_key or not s3_bucket:
                logger.warning(
                    "S3_EXPORT_ENABLED=1, но не указаны S3_ACCESS_KEY, S3_SECRET_KEY или S3_BUCKET. "
                    "S3 экспорт отключен."
                )
            else:
                s3_exporter = S3Exporter(
                    endpoint_url=s3_endpoint_url,
                    access_key=s3_access_key,
                    secret_key=s3_secret_key,
                    bucket=s3_bucket,
                    region=s3_region,
                    prefix=s3_prefix
                )
                
                # Создаем подписчика и регистрируем его
                s3_subscriber = S3ExportSubscriber(
                    s3_exporter=s3_exporter,
                    artifact_service=artifact_service,
                    storage_service=storage_service
                )
                event_service.subscribe(s3_subscriber)
                
                logger.info(
                    f"S3 экспорт включен: bucket={s3_bucket}, "
                    f"endpoint={s3_endpoint_url or 'AWS S3'}, prefix={s3_prefix or '(нет)'}"
                )
        except Exception as e:
            logger.error(f"Ошибка при инициализации S3 экспорта: {e}", exc_info=True)
            logger.warning("Продолжаем работу без S3 экспорта")
            s3_exporter = None
    else:
        logger.info("S3 экспорт отключен (S3_EXPORT_ENABLED != 1)")
        s3_exporter = None
    
    # Инициализация billing
    # КРИТИЧНО: Используем тот же db_path, что и для основной БД (для консистентности)
    # Можно переопределить через PLATFORM_DB_PATH env var
    billing_db_path = os.getenv("PLATFORM_DB_PATH", settings.db_path if settings else "platform.db")
    app.state.billing_service = BillingService(db_path=billing_db_path)
    app.state.billing_service.ensure_schema()
    app.state.billing_service.seed_default_rates_if_empty()
    
    # Регистрируем BillingSubscriber
    billing_subscriber = BillingSubscriber(
        billing_service=app.state.billing_service,
        artifact_service=artifact_service
    )
    event_service.subscribe(billing_subscriber)
    
    # Инициализация entitlements (планы и подписки)
    # КРИТИЧНО: Используем ТОТ ЖЕ db_path, что и billing_service (для консистентности)
    # Это гарантирует, что checkout и webhook работают с одной БД
    entitlement_db_path = os.getenv("PLATFORM_DB_PATH", settings.db_path if settings else "platform.db")
    app.state.entitlement_service = EntitlementService(db_path=entitlement_db_path)
    app.state.entitlement_service.ensure_schema()
    app.state.entitlement_service.seed_default_plans_if_empty()
    
    logger.info("Billing service инициализирован и подписан на события")
    logger.info("Entitlement service инициализирован")
    
    # Регистрация Product/UI layer event subscriber
    from cyberplat.product.infrastructure.artifact_state_subscriber import artifact_state_subscriber
    event_service.subscribe(artifact_state_subscriber)
    logger.info("Product/UI layer event subscriber зарегистрирован")
    
    # Регистрация 1С integration hook (auto jobs from artifacts)
    from cyberplat.integrations.onec_artifact_hook import create_onec_artifact_hook
    from cyberplat.integrations.onec_settings_service import OneCSettingsService
    from cyberplat.integrations.integration_job_service import IntegrationJobService
    from cyberplat.integrations.idempotency_service import IdempotencyService
    
    onec_settings_service = OneCSettingsService()
    integration_job_service = IntegrationJobService()
    idempotency_service = IdempotencyService()
    
    onec_hook = create_onec_artifact_hook(
        settings_service=onec_settings_service,
        job_service=integration_job_service,
        idempotency_service=idempotency_service,
        artifact_service=artifact_service,
        case_service=None  # Можно передать case_service если нужно
    )
    event_service.subscribe(onec_hook)
    logger.info("1С integration artifact hook зарегистрирован")
    
    # Подключение Product/UI router
    from app.api.product import router as product_router
    app.include_router(product_router, prefix="/api/v1", tags=["product"])
    logger.info("Product/UI router подключен")
    
    # Подключение Email Ingestion router
    from app.api.ingest import router as ingest_router
    app.include_router(ingest_router, prefix="/api/v1", tags=["ingest"])
    logger.info("Email ingestion router подключен")
    
    # Подключение Inbox router
    from app.api.inbox import router as inbox_router
    app.include_router(inbox_router, prefix="/api/v1", tags=["inbox"])
    logger.info("Inbox router подключен")
    
    # Подключение Webhooks router
    from app.api.webhooks import router as webhooks_router
    app.include_router(webhooks_router, prefix="/api/v1", tags=["webhooks"])
    logger.info("Webhooks router подключен")
    
    # Подключение Billing Plans router
    from app.api.billing_plans import router as billing_plans_router
    app.include_router(billing_plans_router, prefix="/api/v1", tags=["billing-plans"])
    logger.info("Billing plans router подключен")
    
    # Подключение Tenant Portal router
    from app.api.tenant_portal import router as tenant_portal_router
    app.include_router(tenant_portal_router, prefix="/api/v1", tags=["tenant-portal"])
    logger.info("Tenant portal router подключен")
    
    # Подключение Agent Execution API
    from app.api.agents import router as agents_router
    app.include_router(agents_router, tags=["agents"])
    logger.info("Agent execution router подключен")

    # Execution management endpoints (cancel)
    from app.api.executions import router as executions_router
    app.include_router(executions_router, tags=["executions"])
    logger.info("Executions router подключен")
    
    # Подключение Cases router (уже подключён на import-time, но логируем)
    # Проверяем, не подключён ли уже (для избежания дублирования)
    # Проверяем по наличию маршрута в app.routes (более надёжный способ)
    cases_router_included = any(
        hasattr(r, "path") and "/api/v1/cases" in str(r.path) 
        for r in app.routes
    )
    if not cases_router_included:
        from app.api.cases import router as cases_router
        app.include_router(cases_router, prefix="/api/v1", tags=["cases"])
    logger.info("Cases router подключен")
    
    # Подключение Integrations router
    from app.api.integrations import router as integrations_router
    app.include_router(integrations_router, prefix="/api/v1", tags=["integrations"])
    logger.info("Integrations router подключен")
    
    # Подключение Payments router (уже подключён на import-time, но логируем)
    payments_router_included = any(
        hasattr(r, "path") and "/api/v1/payments" in str(r.path)
        for r in app.routes
    )
    if not payments_router_included:
        from app.api.payments import router as payments_router
        app.include_router(payments_router, prefix="/api/v1", tags=["payments"])
    logger.info("Payments router подключен")
    
    # Подключение Reconciliation router
    from app.api.reconciliation import router as reconciliation_router
    app.include_router(reconciliation_router, prefix="/api/v1", tags=["reconciliation"])
    logger.info("Reconciliation router подключен")
    
    # Start Agent Executor (if enabled)
    from app.agents.executor import start_executor
    from cyberplat.product.infrastructure.database import get_engine
    start_executor(get_engine())
    logger.info("Agent executor initialized")
    
    # Подключение Tenant Portal Web UI
    portal_session_secret = os.getenv("TENANT_PORTAL_SESSION_SECRET", "").strip()
    if portal_session_secret:
        from starlette.middleware.sessions import SessionMiddleware
        app.add_middleware(
            SessionMiddleware,
            secret_key=portal_session_secret,
            same_site="lax",
            https_only=os.getenv("ENV", "dev").lower() == "prod",
        )
        from app.tenant_portal.web import router as tenant_portal_web_router
        app.include_router(tenant_portal_web_router)
        logger.info("Tenant portal web UI enabled at /tenant/")
    else:
        # Still mount routes but they will return 503
        from app.tenant_portal.web import router as tenant_portal_web_router
        app.include_router(tenant_portal_web_router)
        logger.warning("Tenant portal web UI disabled (TENANT_PORTAL_SESSION_SECRET not set)")
    
    # Регистрация webhook subscriber для создания deliveries
    from cyberplat.product.infrastructure.database import get_sessionmaker
    from cyberplat.product.infrastructure.webhook_repositories_sqlalchemy import (
        WebhookRepositoryImpl,
        WebhookDeliveryRepositoryImpl
    )
    from cyberplat.product.infrastructure.webhook_subscriber import create_webhook_subscriber
    from sqlalchemy.orm import Session
    
    SessionLocal = get_sessionmaker()
    session: Session = SessionLocal()
    
    try:
        webhook_repo = WebhookRepositoryImpl(session=session)
        webhook_delivery_repo = WebhookDeliveryRepositoryImpl(session=session)
        
        webhook_subscriber = create_webhook_subscriber(
            webhook_repo=webhook_repo,
            webhook_delivery_repo=webhook_delivery_repo
        )
        
        event_service.subscribe(webhook_subscriber)
        logger.info("Webhook subscriber зарегистрирован")
        
    finally:
        session.close()
    
    # Периодическое обновление backlog метрик (каждые 30 секунд)
    def update_backlog_metrics_periodically():
        """Периодически обновлять backlog метрики."""
        import time
        from cyberplat.money_ops.backlog_metrics import update_backlog_metrics
        
        while not stop_event or not stop_event.is_set():
            try:
                update_backlog_metrics()
            except Exception as e:
                logger.warning(f"Ошибка при обновлении backlog метрик: {e}")
            time.sleep(30)  # Обновляем каждые 30 секунд
    
    if metrics_enabled:
        backlog_thread = threading.Thread(target=update_backlog_metrics_periodically, daemon=True)
        backlog_thread.start()
        logger.info("Backlog metrics updater запущен")
    
    # Периодический запуск stuck detectors (каждые 1 час)
    def run_stuck_detectors_periodically():
        """Периодически запускать stuck detectors."""
        import time
        from cyberplat.money_ops.stuck_detectors import detect_stuck_approvals, detect_stuck_reconciliation
        from cyberplat.payments.payment_service import PaymentService
        from cyberplat.reconciliation.reconciliation_service import ReconciliationService
        from cyberplat.case_service import CaseService
        
        payment_service = PaymentService()
        reconciliation_service = ReconciliationService()
        case_service = CaseService()
        
        while not stop_event or not stop_event.is_set():
            try:
                # Проверяем застрявшие approvals
                stuck_approvals = detect_stuck_approvals(
                    payment_service=payment_service,
                    case_service=case_service,
                    threshold_hours=8
                )
                if stuck_approvals:
                    logger.warning(f"Обнаружено {len(stuck_approvals)} застрявших approvals")
                
                # Проверяем застрявшие reconciliation
                stuck_reconciliation = detect_stuck_reconciliation(
                    reconciliation_service=reconciliation_service,
                    case_service=case_service,
                    threshold_hours=24
                )
                if stuck_reconciliation:
                    logger.warning(f"Обнаружено {len(stuck_reconciliation)} застрявших reconciliation statements")
                
            except Exception as e:
                logger.warning(f"Ошибка при запуске stuck detectors: {e}")
            
            time.sleep(3600)  # Каждый час
    
    stuck_detector_thread = threading.Thread(target=run_stuck_detectors_periodically, daemon=True)
    stuck_detector_thread.start()
    logger.info("Stuck detectors запущены")
    
    logger.info("Приложение готово к работе (API + Worker + Agent Platform + Billing + Entitlements + Product/UI + Webhooks + Money Ops)")


@app.on_event("shutdown")
async def shutdown_event():
    """Очистка при завершении приложения."""
    global worker, stop_event
    
    logger.info("Завершение работы приложения...")
    
    # Останавливаем воркер
    if worker:
        worker.stop()
    
    if stop_event:
        stop_event.set()
    
    logger.info("Приложение остановлено")


# Модели ответов
class ProcessResponse(BaseModel):
    job_id: str
    status: str


class JobResultResponse(BaseModel):
    job_id: str
    status: str
    result: Optional[dict] = None
    stats: Optional[dict] = None
    billing: Optional[dict] = None
    error: Optional[str] = None


class StatsResponse(BaseModel):
    total_runs: int
    cached_runs: int
    total_openai_requests: int
    total_tokens_in: int
    total_tokens_out: int
    total_cost_usd: float
    total_elapsed_ms: int
    avg_elapsed_ms: int
    savings_usd: float  # Экономия за счет кэша


# Модели для платформы агентов
class DocumentUploadResponse(BaseModel):
    artifact_id: str
    file_id: str


class AgentRunRequest(BaseModel):
    artifact_id: str
    file_id: Optional[str] = None
    tenant_id: Optional[str] = None
    options: Optional[Dict[str, Any]] = None


class AgentRunResponse(BaseModel):
    success: bool
    artifact_id: Optional[str] = None
    tenant_id: Optional[str] = None
    error: Optional[str] = None


class PreparePaymentResponse(BaseModel):
    status: str  # "queued" | "processing" | "done"
    job_id: str
    invoice_id: str
    payment_artifact_id: Optional[str] = None


@app.get("/health")
async def health(request: Request):
    """
    Health check endpoint.
    
    Проверяет, что процесс жив и базовые компоненты инициализированы.
    Не проверяет зависимости (БД, внешние сервисы) - для этого используется /ready.
    """
    return {
        "status": "ok",
        "service": "document-processing-api",
        "worker_running": worker.is_running if worker else False,
        "billing_initialized": hasattr(request.app.state, "billing_service") and request.app.state.billing_service is not None
    }


@app.get("/ready")
async def ready(request: Request):
    """
    Readiness check endpoint.
    
    Проверяет доступность критичных зависимостей (БД, сервисы).
    Используется для Kubernetes liveness/readiness probes.
    """
    checks = {
        "status": "ok",
        "checks": {}
    }
    
    # Проверка БД
    original_database_url = get_original_database_url()
    normalized_database_url = normalize_sqlalchemy_database_url(original_database_url)
    
    # Если указан PostgreSQL через DATABASE_URL
    # Проверяем оригинальный URL (до нормализации) для определения типа БД
    is_postgres = original_database_url and (
        original_database_url.startswith("postgresql://") or 
        original_database_url.startswith("postgres://") or 
        original_database_url.startswith("postgresql+")  # postgresql+psycopg://, postgresql+asyncpg://, etc.
    )
    
    if is_postgres:
        try:
            import psycopg
            from urllib.parse import urlparse
            
            # Парсим DATABASE_URL (используем оригинальный для psycopg.connect)
            # Для psycopg.connect используются разобранные параметры подключения,
            # для SQLAlchemy create_engine() — нормализованный DATABASE_URL с postgresql+psycopg://
            parsed = urlparse(original_database_url)
            
            # Подключаемся к PostgreSQL с коротким таймаутом
            conn = psycopg.connect(
                host=parsed.hostname or os.getenv("POSTGRES_HOST", "postgres"),
                port=parsed.port or int(os.getenv("POSTGRES_PORT", "5432")),
                dbname=parsed.path.lstrip("/") if parsed.path else os.getenv("POSTGRES_DB", "agent_platform"),
                user=parsed.username or os.getenv("POSTGRES_USER", "postgres"),
                password=parsed.password or os.getenv("POSTGRES_PASSWORD", ""),
                connect_timeout=3  # Короткий таймаут для readiness check
            )
            
            # Выполняем простой запрос
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
            
            # Проверяем версию схемы Alembic через единую функцию
            # Используем тот же SQLAlchemy engine что и остальное приложение
            try:
                from sqlalchemy import create_engine
                from utils.db_migrations import check_database_migration
                
                # Создаём engine с нормализованным URL (postgresql+psycopg://)
                engine = create_engine(normalized_database_url, pool_pre_ping=True)
                
                # Извлекаем имя драйвера из engine
                driver_name = engine.dialect.driver
                
                # Проверяем миграции через централизованную функцию
                migration_ok, migration_message = check_database_migration(engine)
                
                checks["checks"]["database_driver"] = driver_name
                checks["checks"]["database_migration"] = migration_message
                
                if migration_ok:
                    checks["checks"]["database"] = f"ok (postgresql, driver: {driver_name})"
                else:
                    checks["checks"]["database"] = f"ok (postgresql, driver: {driver_name})"
                    # Миграции не критичны для readiness, но отмечаем degraded если есть pending
                    if "pending" in migration_message:
                        checks["status"] = "degraded"
                
                engine.dispose()
            except Exception as migration_error:
                # Если проверка миграций не удалась, но подключение работает - предупреждение
                logger.warning(f"Failed to check Alembic migration version: {migration_error}")
                checks["checks"]["database"] = "ok (postgresql, migration check failed)"
                checks["checks"]["database_migration"] = f"warning: {str(migration_error)[:50]}"
                checks["checks"]["database_driver"] = "unknown"
            
            conn.close()
        except ImportError:
            checks["checks"]["database"] = "error: psycopg not installed"
            checks["status"] = "degraded"
        except Exception as e:
            checks["checks"]["database"] = f"error: {str(e)[:50]}"
            checks["status"] = "degraded"
    else:
        # SQLite проверка (как раньше)
        try:
            if db:
                # Простая проверка доступности БД
                conn = db._get_connection()
                conn.close()
                checks["checks"]["database"] = "ok (sqlite)"
            else:
                checks["checks"]["database"] = "not_initialized"
        except Exception as e:
            checks["checks"]["database"] = f"error: {str(e)[:50]}"
            checks["status"] = "degraded"
    
    # Проверка billing service
    if hasattr(request.app.state, "billing_service") and request.app.state.billing_service:
        checks["checks"]["billing_service"] = "ok"
    else:
        checks["checks"]["billing_service"] = "not_initialized"
    
    # Проверка entitlement service
    if hasattr(request.app.state, "entitlement_service") and request.app.state.entitlement_service:
        checks["checks"]["entitlement_service"] = "ok"
    else:
        checks["checks"]["entitlement_service"] = "not_initialized"
    
    # Если хотя бы одна критичная проверка не прошла, возвращаем 503
    if checks["status"] != "ok":
        return JSONResponse(status_code=503, content=checks)
    
    return checks


@app.get("/metrics")
async def metrics():
    """
    Prometheus metrics endpoint.
    
    Возвращает метрики в Prometheus exposition format.
    Доступен только если METRICS_ENABLED=1.
    """
    if not metrics_enabled:
        raise HTTPException(status_code=404, detail="Metrics are disabled")
    return metrics_endpoint()


@app.post("/v1/process", response_model=ProcessResponse)
async def process_document(
    file: UploadFile = File(...),
    x_client_id: Optional[str] = Header(None, alias="X-Client-ID")
):
    """
    Поставить задачу на обработку документа в очередь.
    
    - **file**: Файл для обработки (PDF, JPG, PNG и т.д.)
    - **X-Client-ID**: Опциональный идентификатор клиента
    
    Возвращает job_id для проверки статуса через GET /v1/result/{job_id}
    """
    if not queue:
        raise HTTPException(status_code=500, detail="Очередь не инициализирована")
    
    # Генерируем job_id
    job_id = str(uuid.uuid4())
    
    # Читаем файл
    file_bytes = await file.read()
    
    # Сохраняем файл во временную директорию
    temp_dir = Path("out/jobs")
    temp_dir.mkdir(parents=True, exist_ok=True)
    temp_file = temp_dir / f"{job_id}_{file.filename}"
    
    try:
        temp_file.write_bytes(file_bytes)
        
        # Добавляем задачу в очередь
        queue.add_job(
            job_id=job_id,
            filename=file.filename,
            file_path=str(temp_file),
            client_id=x_client_id
        )
        
        logger.info(f"Задача добавлена в очередь: {job_id} ({file.filename})")
        
        return ProcessResponse(
            job_id=job_id,
            status=JobStatus.QUEUED.value
        )
    
    except Exception as e:
        logger.error(f"Ошибка при добавлении задачи: {e}", exc_info=True)
        # Удаляем файл при ошибке
        if temp_file.exists():
            temp_file.unlink()
        raise HTTPException(status_code=500, detail=f"Ошибка при создании задачи: {str(e)}")


@app.get("/v1/result/{job_id}", response_model=JobResultResponse)
async def get_result(job_id: str):
    """
    Получить результат обработки задачи.
    
    - **job_id**: ID задачи, полученный из POST /v1/process
    
    Статусы:
    - `queued` - задача в очереди
    - `processing` - задача обрабатывается
    - `done` - задача завершена успешно (result доступен)
    - `error` - ошибка обработки (error доступен)
    """
    if not queue:
        raise HTTPException(status_code=500, detail="Очередь не инициализирована")
    
    job = queue.get_job(job_id)
    
    if not job:
        raise HTTPException(status_code=404, detail=f"Задача {job_id} не найдена")
    
    response = JobResultResponse(
        job_id=job["job_id"],
        status=job["status"],
        result=job.get("result"),
        stats=job.get("stats"),
        billing=job.get("billing"),
        error=job.get("error_message")
    )
    
    return response




@app.get("/v1/stats", response_model=StatsResponse)
async def get_stats(
    x_client_id: Optional[str] = Header(None, alias="X-Client-ID")
):
    """
    Получить статистику обработки.
    
    - **X-Client-ID**: Опциональный идентификатор клиента для фильтрации
    """
    if not db:
        raise HTTPException(status_code=500, detail="Приложение не инициализировано")
    
    stats = db.get_runs_stats(client_id=x_client_id)
    
    # Рассчитываем экономию (если бы все запросы были без кэша)
    # Это упрощенная оценка - реальная экономия зависит от конкретных токенов
    cached_runs = stats["cached_runs"]
    avg_cost = stats["total_cost_usd"] / stats["total_runs"] if stats["total_runs"] > 0 else 0.0
    savings_usd = cached_runs * avg_cost  # Примерная экономия
    
    return StatsResponse(
        total_runs=stats["total_runs"],
        cached_runs=stats["cached_runs"],
        total_openai_requests=stats["total_openai_requests"],
        total_tokens_in=stats["total_tokens_in"],
        total_tokens_out=stats["total_tokens_out"],
        total_cost_usd=round(stats["total_cost_usd"], 6),
        total_elapsed_ms=stats["total_elapsed_ms"],
        avg_elapsed_ms=stats["avg_elapsed_ms"],
        savings_usd=round(savings_usd, 6)
    )


# API endpoints для платформы агентов
@app.post("/documents/upload", response_model=DocumentUploadResponse)
async def upload_document(
    request: Request,
    file: UploadFile = File(...),
    x_tenant_id: Optional[str] = Header(None, alias="X-Tenant-ID")
):
    """
    Загрузить документ и создать артефакт.
    
    - **file**: Файл для обработки (PDF, JPG, PNG и т.д.)
    - **X-Tenant-ID**: ID тенанта (обязателен)
    
    Returns:
        artifact_id и file_id
    """
    if not artifact_service or not storage_service:
        raise HTTPException(status_code=500, detail="Платформа агентов не инициализирована")
    
    # Проверка обязательного tenant_id
    if not x_tenant_id:
        raise HTTPException(
            status_code=400,
            detail="X-Tenant-ID заголовок обязателен для multi-tenant системы"
        )
    
    # Нормализация tenant_id
    x_tenant_id = x_tenant_id.strip()
    if not x_tenant_id or x_tenant_id == "string":
        raise HTTPException(
            status_code=400,
            detail=f"Некорректный tenant_id: '{x_tenant_id}'. tenant_id не может быть пустым или 'string'"
        )
    
    # Генерируем ID для артефакта и файла
    artifact_id = str(uuid.uuid4())
    file_id = f"{artifact_id}_{file.filename}"
    
    # Автоматическое назначение trial (если ещё не назначен)
    try:
        from cyberplat.product.infrastructure.database import get_sessionmaker
        from cyberplat.product.infrastructure.plan_repositories_sqlalchemy import (
            TenantPlanRepositoryImpl,
            PlanRepositoryImpl
        )
        from cyberplat.product.application.assign_trial_use_case import AssignTrialUseCase
        from sqlalchemy.orm import Session
        
        SessionLocal = get_sessionmaker()
        session: Session = SessionLocal()
        
        try:
            tenant_plan_repo = TenantPlanRepositoryImpl(session=session)
            plan_repo = PlanRepositoryImpl(session=session)
            
            assign_trial_use_case = AssignTrialUseCase(
                tenant_plan_repo=tenant_plan_repo,
                plan_repo=plan_repo
            )
            assign_trial_use_case.execute(tenant_id=x_tenant_id)
        finally:
            session.close()
    except Exception as e:
        # Не падаем, если не удалось назначить trial (логируем)
        logger.warning(f"Failed to assign trial to tenant {x_tenant_id}: {e}")
    
    # Проверка квот (enforcement)
    try:
        from cyberplat.product.application.billing_enforcement_service import (
            BillingEnforcementService,
            QuotaExceededError,
            TrialExpiredError,
            SubscriptionPastDueError,
            SubscriptionCanceledError,
        )
        from cyberplat.product.infrastructure.database import get_sessionmaker
        from cyberplat.product.infrastructure.plan_repositories_sqlalchemy import (
            TenantPlanRepositoryImpl,
            PlanRepositoryImpl
        )
        from sqlalchemy.orm import Session
        
        # Получаем services из app.state или глобальных переменных
        billing_service = None
        event_service = None
        entitlement_service = None
        
        # Пытаемся получить из request.app.state
        if hasattr(request, "app") and hasattr(request.app, "state"):
            billing_service = getattr(request.app.state, "billing_service", None)
            event_service = getattr(request.app.state, "event_service", None)
            entitlement_service = getattr(request.app.state, "entitlement_service", None)
        
        # Fallback на глобальные переменные
        if not billing_service:
            try:
                from app.main import billing_service as global_billing_service
                billing_service = global_billing_service
            except:
                pass
        if not event_service:
            try:
                from app.main import event_service as global_event_service
                event_service = global_event_service
            except:
                pass
        
        if billing_service and event_service:
            # Получаем репозитории для планов
            SessionLocal = get_sessionmaker()
            session: Session = SessionLocal()
            
            try:
                tenant_plan_repo = TenantPlanRepositoryImpl(session=session)
                plan_repo = PlanRepositoryImpl(session=session)
                
                billing_enforcement = BillingEnforcementService(
                    billing_service=billing_service,
                    event_service=event_service,
                    tenant_plan_repo=tenant_plan_repo,
                    plan_repo=plan_repo,
                    entitlement_service=entitlement_service
                )
                
                billing_enforcement.enforce(
                    tenant_id=x_tenant_id,
                    required_metrics={"document_upload": 1.0},
                    operation_name="document_upload"
                )
            finally:
                session.close()
    except TrialExpiredError as e:
        # 402 Payment Required для истёкшего trial
        raise HTTPException(
            status_code=402,
            detail={
                "detail": "Пробный период истёк",
                "trial_expired": True,
                "expires_at": e.expires_at,
                "upgrade_url": "/billing/upgrade"
            }
        )
    except SubscriptionPastDueError as e:
        raise HTTPException(
            status_code=402,
            detail={
                "detail": "Оплата просрочена",
                "subscription_status": "past_due",
                "expires_at": e.expires_at,
                "failed_charges": e.failed_charges,
                "upgrade_url": "/api/v1/billing/checkout/kaspi",
            },
        )
    except SubscriptionCanceledError:
        raise HTTPException(
            status_code=402,
            detail={
                "detail": "Подписка отменена",
                "subscription_status": "canceled",
                "upgrade_url": "/api/v1/billing/checkout/kaspi",
            },
        )
    except QuotaExceededError as e:
        # 402 Payment Required для превышенной квоты
        raise HTTPException(
            status_code=402,
            detail={
                "detail": "Квота превышена",
                "metric": e.metric,
                "period": e.period,
                "used_units": e.used_units,
                "monthly_quota": e.monthly_quota,
                "operation": e.operation,
                "upgrade_url": e.upgrade_url
            }
        )
    except Exception as e:
        # Другие ошибки - пробрасываем дальше
        raise
    
    # Читаем файл
    file_bytes = await file.read()
    
    # Сохраняем файл
    temp_dir = Path("out/jobs")
    temp_dir.mkdir(parents=True, exist_ok=True)
    temp_file = temp_dir / file_id
    
    try:
        temp_file.write_bytes(file_bytes)
        
        # Создаем артефакт для загруженного файла
        artifact_service.create_artifact(
            kind="document",
            source="upload",
            data={"filename": file.filename, "file_id": file_id},
            tenant_id=x_tenant_id,
            # Критично: используем заранее сгенерированный artifact_id,
            # чтобы legacy upload и product/UI слой ссылались на ОДИН и тот же документ.
            artifact_id=artifact_id
        )
        
        # Автоматически создаём artifact_state для документа (ui_status="uploaded")
        # Это гарантирует, что state создан даже если event subscriber не сработал
        try:
            from cyberplat.product.infrastructure.database import get_sessionmaker
            from cyberplat.product.infrastructure.models import ArtifactState
            from datetime import datetime
            
            SessionLocal = get_sessionmaker()
            session = SessionLocal()
            try:
                # Проверяем, не существует ли уже state (idempotent)
                existing_state = session.query(ArtifactState).filter(
                    ArtifactState.artifact_id == artifact_id
                ).first()
                
                if not existing_state:
                    now = datetime.now().isoformat()
                    state = ArtifactState(
                        id=str(uuid.uuid4()),
                        tenant_id=x_tenant_id,
                        artifact_id=artifact_id,
                        ui_status="uploaded",
                        created_at=now,
                        updated_at=now
                    )
                    session.add(state)
                    session.commit()
                    logger.info(f"Created artifact_state for uploaded document: artifact_id={artifact_id}, tenant_id={x_tenant_id}")
                else:
                    # Обновляем статус если нужно
                    if existing_state.ui_status != "uploaded":
                        existing_state.ui_status = "uploaded"
                        existing_state.updated_at = datetime.now().isoformat()
                        session.commit()
            finally:
                session.close()
        except Exception as state_error:
            # Не падаем, если не удалось создать state (event subscriber создаст позже)
            logger.warning(f"Failed to create artifact_state on upload: {state_error}")
        
        logger.info(f"Документ загружен: artifact_id={artifact_id}, file_id={file_id}, tenant_id={x_tenant_id}")
        
        return DocumentUploadResponse(
            artifact_id=artifact_id,
            file_id=file_id
        )
    
    except TenantValidationError as e:
        logger.error(f"Ошибка валидации tenant_id: {e}", exc_info=True)
        if temp_file.exists():
            temp_file.unlink()
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        # Пробрасываем HTTPException (включая 402 Payment Required)
        if temp_file.exists():
            temp_file.unlink()
        raise
    except Exception as e:
        logger.error(f"Ошибка при загрузке документа: {e}", exc_info=True)
        if temp_file.exists():
            temp_file.unlink()
        raise HTTPException(status_code=500, detail=f"Ошибка при загрузке документа: {str(e)}")


@app.post("/agents/doc_agent/run", response_model=AgentRunResponse)
async def run_doc_agent(
    request: Request,
    agent_request: AgentRunRequest,
    x_tenant_id: Optional[str] = Header(None, alias="X-Tenant-ID")
):
    """
    Запустить doc_agent для обработки документа.
    
    - **artifact_id**: ID артефакта документа (обязателен)
    - **file_id**: ID файла (опционально, если не указан, будет использован artifact_id)
    - **tenant_id**: ID тенанта (опционально, можно передать в заголовке X-Tenant-ID)
    
    Примечание: tenant_id будет получен из артефакта, если не передан явно.
    """
    if not agent_registry or not artifact_service:
        raise HTTPException(status_code=500, detail="Платформа агентов не инициализирована")
    
    # Получаем агента
    agent = agent_registry.get("doc_agent")
    if not agent:
        raise HTTPException(status_code=404, detail="Агент doc_agent не найден")
    
    # Определяем tenant_id (приоритет: из запроса > из заголовка)
    tenant_id = agent_request.tenant_id or x_tenant_id
    
    # Если tenant_id передан - нормализуем
    if tenant_id:
        tenant_id = tenant_id.strip()
        if not tenant_id or tenant_id == "string":
            raise HTTPException(
                status_code=400,
                detail=f"Некорректный tenant_id: '{tenant_id}'. tenant_id не может быть пустым или 'string'"
            )
    
    # Определяем file_id (приоритет: из запроса > artifact_id)
    file_id = agent_request.file_id or agent_request.artifact_id
    
    # Проверка квот для doc_agent (enforcement через BillingEnforcementService)
    if tenant_id:
        try:
            from cyberplat.product.application.billing_enforcement_service import (
                BillingEnforcementService,
                QuotaExceededError,
                TrialExpiredError,
                SubscriptionPastDueError,
                SubscriptionCanceledError,
            )
            from cyberplat.product.infrastructure.database import get_sessionmaker
            from cyberplat.product.infrastructure.plan_repositories_sqlalchemy import (
                TenantPlanRepositoryImpl,
                PlanRepositoryImpl,
            )
            from sqlalchemy.orm import Session
            
            billing_service = getattr(request.app.state, "billing_service", None)
            event_service = getattr(request.app.state, "event_service", None)
            entitlement_service = getattr(request.app.state, "entitlement_service", None)
            
            if billing_service and event_service:
                SessionLocal = get_sessionmaker()
                session: Session = SessionLocal()
                try:
                    tenant_plan_repo = TenantPlanRepositoryImpl(session=session)
                    plan_repo = PlanRepositoryImpl(session=session)
                    billing_enforcement = BillingEnforcementService(
                        billing_service=billing_service,
                        event_service=event_service,
                        tenant_plan_repo=tenant_plan_repo,
                        plan_repo=plan_repo,
                        entitlement_service=entitlement_service,
                    )
                    
                    # Для MVP: page_processed=1 (можно расширить позже для реального количества страниц)
                    billing_enforcement.enforce(
                        tenant_id=tenant_id,
                        required_metrics={
                            "invoice_extracted": 1.0,
                            "page_processed": 1.0
                        },
                        operation_name="doc_agent_run"
                    )
                finally:
                    session.close()
                
        except Exception as e:
            if isinstance(e, TrialExpiredError):
                raise HTTPException(
                    status_code=402,
                    detail={
                        "detail": "Пробный период истёк",
                        "trial_expired": True,
                        "expires_at": getattr(e, "expires_at", None),
                        "upgrade_url": "/billing/upgrade",
                    },
                )
            if isinstance(e, SubscriptionPastDueError):
                raise HTTPException(
                    status_code=402,
                    detail={
                        "detail": "Оплата просрочена",
                        "subscription_status": "past_due",
                        "expires_at": getattr(e, "expires_at", None),
                        "failed_charges": getattr(e, "failed_charges", None),
                        "upgrade_url": "/api/v1/billing/checkout/kaspi",
                    },
                )
            if isinstance(e, SubscriptionCanceledError):
                raise HTTPException(
                    status_code=402,
                    detail={
                        "detail": "Подписка отменена",
                        "subscription_status": "canceled",
                        "upgrade_url": "/api/v1/billing/checkout/kaspi",
                    },
                )
            if isinstance(e, QuotaExceededError):
                raise HTTPException(
                    status_code=402,
                    detail={
                        "detail": "Квота превышена",
                        "metric": e.metric,
                        "period": e.period,
                        "used_units": e.used_units,
                        "monthly_quota": e.monthly_quota,
                        "operation": e.operation,
                        "upgrade_url": e.upgrade_url
                    }
                )
            # Другие ошибки - пробрасываем дальше
            raise
    
    # Создаем контекст
    context = AgentContext(
        artifact_id=agent_request.artifact_id,
        file_id=file_id,
        tenant_id=tenant_id
    )
    
    try:
        # Запускаем агента
        result = await agent.run(context)
        
        return AgentRunResponse(
            success=result.get("success", True),
            artifact_id=result.get("artifact_id"),
            tenant_id=result.get("tenant_id"),
            error=None
        )
    
    except TenantValidationError as e:
        logger.error(f"Ошибка валидации tenant_id: {e}", exc_info=True)
        return AgentRunResponse(
            success=False,
            artifact_id=None,
            tenant_id=tenant_id,
            error=str(e)
        )
    except Exception as e:
        logger.error(f"Ошибка при выполнении doc_agent: {e}", exc_info=True)
        return AgentRunResponse(
            success=False,
            artifact_id=None,
            tenant_id=tenant_id,
            error=str(e)
        )


@app.get("/api/v1/agents")
async def list_agents():
    """
    Получить список всех зарегистрированных агентов.
    """
    if not agent_registry:
        raise HTTPException(status_code=500, detail="Платформа агентов не инициализирована")
    
    agents = agent_registry.list_agents()
    return {"agents": agents}


@app.post("/api/v1/agents/payment_agent/run", response_model=AgentRunResponse)
async def run_payment_agent(
    request: AgentRunRequest,
    x_tenant_id: Optional[str] = Header(None, alias="X-Tenant-ID")
):
    """
    Запустить payment_agent для создания payment артефакта из invoice артефакта.
    
    - **artifact_id**: ID invoice артефакта (обязателен)
    - **tenant_id**: ID тенанта (опционально, можно передать в заголовке X-Tenant-ID)
    - **options**: Дополнительные опции (опционально)
    
    Примечание: tenant_id будет получен из invoice артефакта, если не передан явно.
    """
    if not agent_registry or not artifact_service:
        raise HTTPException(status_code=500, detail="Платформа агентов не инициализирована")
    
    # Получаем агента
    agent = agent_registry.get("payment_agent")
    if not agent:
        raise HTTPException(status_code=404, detail="Агент payment_agent не найден")
    
    # Определяем tenant_id (приоритет: из запроса > из заголовка)
    tenant_id = request.tenant_id or x_tenant_id
    
    # Если tenant_id передан - нормализуем
    if tenant_id:
        tenant_id = tenant_id.strip()
        if not tenant_id or tenant_id == "string":
            raise HTTPException(
                status_code=400,
                detail=f"Некорректный tenant_id: '{tenant_id}'. tenant_id не может быть пустым или 'string'"
            )
    
    # Создаем контекст
    context = AgentContext(
        artifact_id=request.artifact_id,
        file_id=None,  # payment_agent не требует file_id
        tenant_id=tenant_id
    )
    
    try:
        # Запускаем агента
        result = await agent.run(context)
        
        return AgentRunResponse(
            success=result.get("success", True),
            artifact_id=result.get("artifact_id"),
            tenant_id=result.get("tenant_id"),
            error=None
        )
    
    except TenantValidationError as e:
        logger.error(f"Ошибка валидации tenant_id: {e}", exc_info=True)
        return AgentRunResponse(
            success=False,
            artifact_id=None,
            tenant_id=tenant_id,
            error=str(e)
        )
    except Exception as e:
        logger.error(f"Ошибка при выполнении payment_agent: {e}", exc_info=True)
        return AgentRunResponse(
            success=False,
            artifact_id=None,
            tenant_id=tenant_id,
            error=str(e)
        )


@app.get("/api/v1/artifacts/{artifact_id}/events")
async def get_artifact_events(artifact_id: str):
    """
    Получить все события для артефакта.
    
    - **artifact_id**: ID артефакта
    """
    if not event_service:
        raise HTTPException(status_code=500, detail="Платформа агентов не инициализирована")
    
    import json
    
    conn = event_service._get_connection()
    cur = conn.cursor()
    
    cur.execute("""
        SELECT id, event_type, tenant_id, artifact_id, payload, created_at
        FROM events
        WHERE artifact_id = ?
        ORDER BY created_at ASC
    """, (artifact_id,))
    
    rows = cur.fetchall()
    conn.close()
    
    events = []
    for row in rows:
        event = {
            "id": row["id"],
            "event_type": row["event_type"],
            "tenant_id": row["tenant_id"],
            "artifact_id": row["artifact_id"],
            "payload": json.loads(row["payload"]) if row["payload"] else None,
            "created_at": row["created_at"]
        }
        events.append(event)
    
    return events


@app.post("/api/v1/invoices/{invoice_id}/prepare-payment", response_model=PreparePaymentResponse)
async def prepare_payment_from_invoice(
    request: Request,
    invoice_id: str,
    x_tenant_id: Optional[str] = Header(None, alias="X-Tenant-ID")
):
    """
    Подготовить payment артефакт из invoice артефакта.
    
    - **invoice_id**: ID invoice артефакта
    - **X-Tenant-ID**: ID тенанта (обязателен)
    
    Валидирует, что invoice принадлежит указанному tenant, затем запускает payment_agent.
    """
    if not artifact_service or not agent_registry:
        raise HTTPException(status_code=500, detail="Платформа агентов не инициализирована")
    
    # Проверка обязательного tenant_id
    if not x_tenant_id:
        raise HTTPException(
            status_code=400,
            detail="X-Tenant-ID заголовок обязателен для multi-tenant системы"
        )
    
    # Нормализация tenant_id
    x_tenant_id = x_tenant_id.strip()
    if not x_tenant_id or x_tenant_id == "string":
        raise HTTPException(
            status_code=400,
            detail=f"Некорректный tenant_id: '{x_tenant_id}'. tenant_id не может быть пустым или 'string'"
        )
    
    # Загружаем invoice артефакт
    invoice_artifact = artifact_service.get_artifact(invoice_id)
    if not invoice_artifact:
        raise HTTPException(
            status_code=404,
            detail=f"Invoice артефакт {invoice_id} не найден"
        )
    
    # Валидация kind
    if invoice_artifact.get("kind") != "invoice":
        raise HTTPException(
            status_code=400,
            detail=f"Артефакт {invoice_id} не является invoice (kind={invoice_artifact.get('kind')})"
        )
    
    # Валидация tenant_id (tenant safety)
    invoice_tenant_id = invoice_artifact.get("tenant_id")
    if not invoice_tenant_id:
        raise HTTPException(
            status_code=403,
            detail=f"Invoice артефакт {invoice_id} не имеет tenant_id. Cross-tenant access запрещен."
        )
    
    if invoice_tenant_id != x_tenant_id:
        raise HTTPException(
            status_code=403,
            detail=f"Invoice артефакт {invoice_id} принадлежит другому tenant "
                   f"({invoice_tenant_id} != {x_tenant_id}). Cross-tenant access запрещен."
        )
    
    # Получаем payment_agent
    agent = agent_registry.get("payment_agent")
    if not agent:
        raise HTTPException(status_code=500, detail="Агент payment_agent не найден")
    
    # Проверка квоты для payment_agent (payment_prepared)
    bs = getattr(request.app.state, "billing_service", None)
    if bs:
        period = datetime.now().strftime("%Y-%m")
        is_allowed, error_msg = bs.check_quota(invoice_tenant_id, "payment_prepared", period)
        if not is_allowed:
            raise HTTPException(status_code=402, detail=error_msg)
    
    # Генерируем job_id для трекинга
    job_id = str(uuid.uuid4())
    
    try:
        # Создаем контекст с tenant_id из invoice (для безопасности)
        context = AgentContext(
            artifact_id=invoice_id,
            file_id=None,
            tenant_id=invoice_tenant_id  # Используем tenant_id из invoice, а не из заголовка
        )
        
        # Запускаем payment_agent синхронно
        logger.info(f"Запуск payment_agent для invoice {invoice_id} (job_id={job_id}, tenant_id={invoice_tenant_id})")
        result = await agent.run(context)
        
        payment_artifact_id = result.get("artifact_id")
        validation = result.get("validation", {})
        is_ready = validation.get("is_ready", False)
        
        logger.info(f"Payment артефакт создан: {payment_artifact_id} (job_id={job_id}, is_ready={is_ready})")
        
        return PreparePaymentResponse(
            status="done",
            job_id=job_id,
            invoice_id=invoice_id,
            payment_artifact_id=payment_artifact_id
        )
    
    except TenantValidationError as e:
        logger.error(f"Ошибка валидации tenant_id при подготовке payment: {e}", exc_info=True)
        raise HTTPException(
            status_code=403,
            detail=f"Ошибка валидации tenant_id: {str(e)}"
        )
    except Exception as e:
        logger.error(f"Ошибка при подготовке payment для invoice {invoice_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Ошибка при подготовке payment: {str(e)}"
        )


@app.get("/api/v1/jobs/{job_id}/result")
async def get_job_result(job_id: str):
    """
    Получить результат job по job_id.
    
    - **job_id**: ID job (из prepare-payment или других операций)
    
    Для payment jobs возвращает:
    {
        "payment_artifact_id": "...",
        "status": "ready" | "invalid"
    }
    
    Примечание: для синхронных операций (prepare-payment) результат возвращается сразу в response.
    Этот endpoint можно использовать для будущих асинхронных операций.
    """
    # Для синхронных операций (как prepare-payment) job_id используется только для трекинга
    # Результат возвращается сразу в prepare-payment response
    # Этот endpoint можно использовать для будущих асинхронных операций
    
    # Пока возвращаем информацию о том, что job_id был использован
    # В будущем можно добавить хранение job результатов в БД
    return {
        "job_id": job_id,
        "status": "done",
        "message": "Для синхронных операций результат возвращается сразу в prepare-payment response"
    }


# ============================================
# Product/UI Layer endpoints moved to app/api/product.py
# Router подключен в startup_event через app.include_router()
# ============================================


@app.get("/files/{file_id}")
async def get_file(
    request: Request,
    file_id: str,
    x_tenant_id: Optional[str] = Header(None, alias="X-Tenant-ID")
):
    """
    Получить файл по file_id.
    
    - **X-Tenant-ID**: ID тенанта (обязателен)
    - **file_id**: ID файла
    """
    if not x_tenant_id:
        raise HTTPException(status_code=400, detail="X-Tenant-ID заголовок обязателен")
    
    x_tenant_id = x_tenant_id.strip()
    if not x_tenant_id or x_tenant_id == "string":
        raise HTTPException(status_code=400, detail=f"Некорректный tenant_id: '{x_tenant_id}'")
    
    storage_service = getattr(request.app.state, "storage_service", None)
    if not storage_service:
        raise HTTPException(status_code=500, detail="Storage service not initialized")
    
    # Получаем путь к файлу
    file_path = storage_service.get_file_path(file_id)
    if not file_path or not file_path.exists():
        raise HTTPException(status_code=404, detail=f"File {file_id} not found")
    
    # TODO: Проверка tenant_id через artifact (если file_id связан с artifact)
    # Пока возвращаем файл без дополнительной проверки
    
    from fastapi.responses import FileResponse
    
    return FileResponse(
        path=str(file_path),
        filename=file_path.name,
        media_type="application/pdf" if file_path.suffix == ".pdf" else "application/octet-stream"
    )


@app.get("/api/v1/artifacts/{artifact_id}")
async def get_artifact(artifact_id: str):
    """
    Получить артефакт по ID.
    
    - **artifact_id**: ID артефакта
    """
    if not artifact_service:
        raise HTTPException(status_code=500, detail="Платформа агентов не инициализирована")
    
    artifact = artifact_service.get_artifact(artifact_id)
    if not artifact:
        raise HTTPException(status_code=404, detail=f"Артефакт {artifact_id} не найден")
    
    return artifact


# Billing API endpoints
class RateCreateRequest(BaseModel):
    metric: str
    unit_price_minor: int
    currency: str = "USD"
    active: bool = True
    monthly_quota: Optional[int] = None


class ResetUsageRequest(BaseModel):
    tenant_id: str
    period: str


class MetricQuotaStatus(BaseModel):
    used_units: float
    monthly_quota: Optional[int]
    remaining_units: Optional[int]
    is_exceeded: bool
    unit_price_minor: int
    amount_minor: int


class QuotaResponse(BaseModel):
    tenant_id: str
    period: str
    currency: str
    metrics: Dict[str, MetricQuotaStatus]


# Billing Portal models
class PlanInfo(BaseModel):
    id: str
    name: str


class SubscriptionInfo(BaseModel):
    status: str
    subscription_id: Optional[str]


class PortalLinks(BaseModel):
    upgrade_url: Optional[str]
    manage_url: Optional[str]


class BillingPortalResponse(BaseModel):
    tenant_id: str
    period: str
    plan: PlanInfo
    subscription: SubscriptionInfo
    invoice: Dict[str, Any]
    quota: Dict[str, Any]
    links: PortalLinks
    trial: Optional[Dict[str, Any]] = None  # Trial информация (days_left, expires_at)
    upgrade_available: bool = True  # Доступен ли upgrade
    expires_at: Optional[str] = None  # Для paid/trial
    subscription_status: Optional[str] = None  # trial|active|past_due|canceled
    failed_charges: Optional[int] = None
    days_left: Optional[int] = None


# Kaspi checkout models
class KaspiCheckoutRequest(BaseModel):
    plan_id: str


class KaspiCheckoutResponse(BaseModel):
    checkout_url: str
    order_id: str
    kaspi_order_id: Optional[str] = None


class KaspiRecurringChargeResponse(BaseModel):
    status: str
    charged: int
    failed: int
    skipped: int
    errors: List[str]


@app.get("/api/v1/billing/rates")
async def get_billing_rates(
    request: Request,
    x_tenant_id: Optional[str] = Header(None, alias="X-Tenant-ID")
):
    """
    Получить тарифы для tenant (tenant-specific + default).
    
    - **X-Tenant-ID**: ID тенанта (обязателен)
    """
    bs = getattr(request.app.state, "billing_service", None)
    if not bs:
        raise HTTPException(status_code=500, detail="Billing not initialized")
    
    if not x_tenant_id:
        raise HTTPException(
            status_code=400,
            detail="X-Tenant-ID заголовок обязателен"
        )
    
    x_tenant_id = x_tenant_id.strip()
    if not x_tenant_id or x_tenant_id == "string":
        raise HTTPException(
            status_code=400,
            detail=f"Некорректный tenant_id: '{x_tenant_id}'"
        )
    
    rates = bs.list_rates(x_tenant_id)
    return rates


@app.post("/api/v1/billing/rates")
async def create_billing_rate(
    request: Request,
    rate_request: RateCreateRequest,
    x_tenant_id: Optional[str] = Header(None, alias="X-Tenant-ID")
):
    """
    Создать или обновить тариф (tenant-specific или default).
    
    - **X-Tenant-ID**: ID тенанта (опционально, если не указан - создается default тариф)
    - **metric**: Метрика (например, "invoice_extracted")
    - **unit_price_minor**: Цена за единицу (в minor units, например cents)
    - **currency**: Валюта (по умолчанию "USD")
    - **active**: Активен ли тариф (по умолчанию True)
    - **monthly_quota**: Месячная квота (опционально)
    """
    import sqlite3
    import traceback
    
    bs = getattr(request.app.state, "billing_service", None)
    if not bs:
        raise HTTPException(status_code=500, detail="Billing not initialized")
    
    # Нормализация tenant_id (может быть None для default rates)
    tenant_id = None
    if x_tenant_id:
        tenant_id = x_tenant_id.strip()
        if not tenant_id or tenant_id == "string":
            raise HTTPException(
                status_code=400,
                detail=f"Некорректный tenant_id: '{x_tenant_id}'"
            )
    
    try:
        rate_id = bs.upsert_rate(
            tenant_id=tenant_id,
            metric=rate_request.metric,
            unit_price_minor=rate_request.unit_price_minor,
            currency=rate_request.currency,
            active=rate_request.active,
            monthly_quota=rate_request.monthly_quota
        )
        
        return {"rate_id": rate_id, "message": "Тариф создан/обновлен"}
    
    except (sqlite3.IntegrityError, sqlite3.OperationalError) as e:
        # Ошибки БД (constraint violations, syntax errors) → HTTP 400
        logger.warning(f"Ошибка БД при создании тарифа: {e}")
        raise HTTPException(
            status_code=400,
            detail=f"Ошибка при создании тарифа: {str(e)}"
        )
    except Exception as e:
        # Остальные ошибки → HTTP 500 с логированием
        logger.error(f"Неожиданная ошибка при создании тарифа: {e}", exc_info=True)
        traceback.print_exc()
        raise HTTPException(
            status_code=500,
            detail=f"Внутренняя ошибка сервера: {str(e)}"
        )


@app.get("/api/v1/billing/usage")
async def get_billing_usage(
    request: Request,
    period: str,
    x_tenant_id: Optional[str] = Header(None, alias="X-Tenant-ID")
):
    """
    Получить использование за период.
    
    - **X-Tenant-ID**: ID тенанта (обязателен)
    - **period**: Период в формате YYYY-MM (например, "2026-01")
    """
    bs = getattr(request.app.state, "billing_service", None)
    if not bs:
        raise HTTPException(status_code=500, detail="Billing not initialized")
    
    if not x_tenant_id:
        raise HTTPException(
            status_code=400,
            detail="X-Tenant-ID заголовок обязателен"
        )
    
    x_tenant_id = x_tenant_id.strip()
    if not x_tenant_id or x_tenant_id == "string":
        raise HTTPException(
            status_code=400,
            detail=f"Некорректный tenant_id: '{x_tenant_id}'"
        )
    
    # Валидация формата периода
    try:
        datetime.strptime(period, "%Y-%m")
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Некорректный формат периода: '{period}'. Ожидается YYYY-MM"
        )
    
    usage = bs.get_usage(tenant_id=x_tenant_id, period=period)
    return usage


@app.get("/api/v1/billing/summary")
async def get_billing_summary(
    request: Request,
    from_period: str,
    to_period: str,
    x_tenant_id: Optional[str] = Header(None, alias="X-Tenant-ID")
):
    """
    Получить агрегированную сводку по периодам.
    
    - **X-Tenant-ID**: ID тенанта (обязателен)
    - **from_period**: Начальный период в формате YYYY-MM
    - **to_period**: Конечный период в формате YYYY-MM
    """
    bs = getattr(request.app.state, "billing_service", None)
    if not bs:
        raise HTTPException(status_code=500, detail="Billing not initialized")
    
    if not x_tenant_id:
        raise HTTPException(
            status_code=400,
            detail="X-Tenant-ID заголовок обязателен"
        )
    
    x_tenant_id = x_tenant_id.strip()
    if not x_tenant_id or x_tenant_id == "string":
        raise HTTPException(
            status_code=400,
            detail=f"Некорректный tenant_id: '{x_tenant_id}'"
        )
    
    # Валидация формата периодов
    try:
        datetime.strptime(from_period, "%Y-%m")
        datetime.strptime(to_period, "%Y-%m")
    except ValueError as e:
        raise HTTPException(
            status_code=400,
            detail=f"Некорректный формат периода. Ожидается YYYY-MM: {str(e)}"
        )
    
    if from_period > to_period:
        raise HTTPException(
            status_code=400,
            detail=f"from_period ({from_period}) должен быть <= to_period ({to_period})"
        )
    
    summary = bs.get_summary(
        tenant_id=x_tenant_id,
        from_period=from_period,
        to_period=to_period
    )
    return summary


# Webhook endpoints
@app.post("/api/v1/billing/webhook/stripe")
async def stripe_webhook(request: Request):
    """
    Обработчик Stripe webhook событий.
    
    Требует заголовок Stripe-Signature для проверки подписи.
    Секрет берется из env: STRIPE_WEBHOOK_SECRET
    
    ВАЖНО: Использует ProcessWebhookUseCase для обработки событий (Clean Architecture).
    """
    import os
    
    entitlement_svc = getattr(request.app.state, "entitlement_service", None)
    if not entitlement_svc:
        raise HTTPException(status_code=503, detail="Entitlement service not initialized")
    
    webhook_secret = os.getenv("STRIPE_WEBHOOK_SECRET", "").strip()
    if not webhook_secret:
        logger.warning("STRIPE_WEBHOOK_SECRET не установлен, webhook отключен")
        raise HTTPException(status_code=503, detail="Stripe webhook secret not configured")
    
    # Получаем подпись из заголовка
    signature = request.headers.get("Stripe-Signature")
    if not signature:
        raise HTTPException(status_code=400, detail="Missing Stripe-Signature header")
    
    # Читаем тело запроса
    body = await request.body()
    
    # Парсим JSON
    try:
        event = json.loads(body.decode('utf-8'))
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {str(e)}")
    
    event_id = event.get("id")
    if not event_id:
        raise HTTPException(status_code=400, detail="Missing event.id in payload")
    
    # Инициализируем Clean Architecture компоненты
    from cyberplat.billing.infrastructure.stripe_provider import StripePaymentProvider
    from cyberplat.billing.infrastructure.repositories import (
        EntitlementSubscriptionRepository,
        EntitlementWebhookEventRepository
    )
    from cyberplat.billing.infrastructure.stripe_webhook_handlers import create_stripe_event_handlers
    from cyberplat.billing.application.process_webhook_use_case import ProcessWebhookUseCase
    
    # Создаем адаптеры
    provider = StripePaymentProvider()
    subscription_repo = EntitlementSubscriptionRepository(entitlement_svc)
    event_repo = EntitlementWebhookEventRepository(entitlement_svc)
    
    # Создаем обработчики событий
    event_handlers = create_stripe_event_handlers()
    
    # Создаем use case
    use_case = ProcessWebhookUseCase(
        provider=provider,
        event_repo=event_repo,
        subscription_repo=subscription_repo,
        event_handlers=event_handlers
    )
    
    # Извлекаем тип события для метрик
    event_type = event.get("type", "unknown")
    
    # Обрабатываем событие через use case
    try:
        success, tenant_id, error_msg = use_case.execute(
            event=event,
            raw_payload=body,
            signature=signature,
            webhook_secret=webhook_secret
        )
        
        # Записываем метрику webhook события
        if metrics_enabled:
            if success:
                if tenant_id is None:
                    status = "duplicate"  # Идемпотентный ответ
                else:
                    status = "ok"
            else:
                if "not handled" in (error_msg or "") or "Missing" in (error_msg or ""):
                    status = "invalid"
                else:
                    status = "error"
            record_webhook_event("stripe", event_type, status)
        
        # Формируем ответ в том же формате, что и раньше (для обратной совместимости)
        if success:
            # Если tenant_id None, это может быть идемпотентный ответ (событие уже обработано)
            if tenant_id is None:
                return {"status": "ok", "message": "Event already processed"}
            return {"status": "ok", "event_id": event_id, "tenant_id": tenant_id}
        else:
            # Событие проигнорировано или ошибка
            if error_msg:
                if "not handled" in error_msg or "Missing" in error_msg:
                    # Помечаем как ignored через event_repo
                    try:
                        event_repo.mark_event_ignored(
                            provider="stripe",
                            event_id=event_id,
                            reason=error_msg
                        )
                    except Exception as e:
                        logger.warning(f"Failed to mark event as ignored: {e}")
                else:
                    # Ошибка обработки - помечаем как processed с ошибкой
                    try:
                        event_repo.mark_event_processed(
                            provider="stripe",
                            event_id=event_id,
                            tenant_id=tenant_id
                        )
                        # Обновляем error_message если нужно
                        conn = entitlement_svc._get_connection()
                        cur = conn.cursor()
                        cur.execute("""
                            UPDATE billing_webhook_events
                            SET error_message = ?
                            WHERE event_id = ? AND provider = ?
                        """, (error_msg, event_id, "stripe"))
                        conn.commit()
                        conn.close()
                    except Exception as e:
                        logger.warning(f"Failed to update event error: {e}")
            return {"status": "ignored", "event_id": event_id, "reason": error_msg}
            
    except Exception as e:
        logger.error(f"Ошибка при обработке Stripe webhook: {e}", exc_info=True)
        
        # Записываем метрику ошибки
        if metrics_enabled:
            event_type = event.get("type", "unknown")
            record_webhook_event("stripe", event_type, "error")
        
        # Помечаем событие как processed с ошибкой
        try:
            event_repo.mark_event_processed(
                provider="stripe",
                event_id=event_id,
                tenant_id=None
            )
            conn = entitlement_svc._get_connection()
            cur = conn.cursor()
            cur.execute("""
                UPDATE billing_webhook_events
                SET error_message = ?
                WHERE event_id = ? AND provider = ?
            """, (str(e), event_id, "stripe"))
            conn.commit()
            conn.close()
        except Exception as update_error:
            logger.warning(f"Failed to update event error: {update_error}")
        raise HTTPException(status_code=500, detail=f"Error processing webhook: {str(e)}")


@app.post("/api/v1/billing/checkout/kaspi", response_model=KaspiCheckoutResponse)
async def kaspi_checkout(
    request: Request,
    checkout_request: KaspiCheckoutRequest,
    x_tenant_id: Optional[str] = Header(None, alias="X-Tenant-ID")
):
    """
    Создать Kaspi checkout session для upgrade плана.
    
    - **X-Tenant-ID**: ID тенанта (обязателен)
    - **plan_id**: ID целевого плана (plan_pro, plan_enterprise)
    """
    import os
    
    entitlement_svc = getattr(request.app.state, "entitlement_service", None)
    if not entitlement_svc:
        raise HTTPException(status_code=500, detail="Entitlement service not initialized")
    
    # Проверка, включен ли Kaspi
    kaspi_enabled = os.getenv("KASPI_ENABLED", "0").strip() == "1"
    if not kaspi_enabled:
        raise HTTPException(status_code=501, detail="Kaspi payment provider is not enabled")
    
    # Проверка обязательного tenant_id
    if not x_tenant_id:
        raise HTTPException(
            status_code=400,
            detail="X-Tenant-ID заголовок обязателен"
        )
    
    x_tenant_id = x_tenant_id.strip()
    if not x_tenant_id or x_tenant_id == "string":
        raise HTTPException(
            status_code=400,
            detail=f"Некорректный tenant_id: '{x_tenant_id}'"
        )
    
    # Валидация plan_id
    # Backward compatible:
    # - legacy: plan_pro / plan_enterprise (used by legacy billing_plans + recurring)
    # - new (plans/tenant_plans): pro / enterprise
    requested_plan_id = (checkout_request.plan_id or "").strip()
    if not requested_plan_id:
        raise HTTPException(status_code=400, detail="plan_id обязателен")

    if requested_plan_id in ["pro", "enterprise"]:
        plan_id = requested_plan_id  # plans.id
        legacy_plan_id = "plan_pro" if requested_plan_id == "pro" else "plan_enterprise"
    elif requested_plan_id in ["plan_pro", "plan_enterprise"]:
        legacy_plan_id = requested_plan_id
        plan_id = "pro" if requested_plan_id == "plan_pro" else "enterprise"
    else:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Некорректный plan_id: '{requested_plan_id}'. "
                f"Допустимые значения: pro, enterprise (или legacy: plan_pro, plan_enterprise)"
            ),
        )

    # Проверяем, что план существует и активен в новой таблице plans
    try:
        from cyberplat.product.infrastructure.database import get_sessionmaker
        from cyberplat.product.infrastructure.plan_repositories_sqlalchemy import PlanRepositoryImpl
        from sqlalchemy.orm import Session

        SessionLocal = get_sessionmaker()
        session: Session = SessionLocal()
        try:
            plan_repo = PlanRepositoryImpl(session=session)
            plan_row = plan_repo.get_plan(plan_id)
            if not plan_row or not plan_row.get("active"):
                raise HTTPException(status_code=404, detail=f"План {plan_id} не найден или не активен")
            # Цена берем из plans (источник истины для upgrade)
            amount_minor = plan_row.get("price_minor")
            currency = plan_row.get("currency") or "KZT"
            if amount_minor is None:
                raise HTTPException(status_code=400, detail=f"План {plan_id} не является платным")
        finally:
            session.close()
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to validate plan via plans table: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to validate plan")
    
    # Проверяем upgrade path по новой системе планов (tenant_plans), но не ломаем legacy:
    # если tenant_plans еще нет — считаем текущим "trial"
    try:
        from cyberplat.product.infrastructure.database import get_sessionmaker
        from cyberplat.product.infrastructure.plan_repositories_sqlalchemy import TenantPlanRepositoryImpl
        from sqlalchemy.orm import Session
        from cyberplat.product.application.assign_trial_use_case import AssignTrialUseCase
        from cyberplat.product.infrastructure.plan_repositories_sqlalchemy import PlanRepositoryImpl

        SessionLocal = get_sessionmaker()
        session: Session = SessionLocal()
        try:
            tenant_plan_repo = TenantPlanRepositoryImpl(session=session)
            plan_repo = PlanRepositoryImpl(session=session)
            # Авто-trial на первом контакте
            AssignTrialUseCase(tenant_plan_repo=tenant_plan_repo, plan_repo=plan_repo).execute(x_tenant_id)

            current_tp = tenant_plan_repo.get_tenant_plan(x_tenant_id)
            current_plan = current_tp["plan_id"] if current_tp else "trial"
            if current_plan == "enterprise":
                raise HTTPException(status_code=400, detail="Уже используется максимальный план enterprise")
            if current_plan == "pro" and plan_id != "enterprise":
                raise HTTPException(status_code=400, detail="С pro доступен upgrade только на enterprise")
            if current_plan == "trial" and plan_id not in ("pro", "enterprise"):
                raise HTTPException(status_code=400, detail="С trial доступен upgrade только на pro или enterprise")
        finally:
            session.close()
    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"Failed to validate upgrade path via tenant_plans: {e}")
    
    # Создаем заказ
    order_id = entitlement_svc.create_order(
        tenant_id=x_tenant_id,
        provider="kaspi",
        plan_id=legacy_plan_id,
        amount_minor=amount_minor,
        currency=currency
    )
    
    # Создаем Kaspi checkout session
    base_url = str(request.base_url).rstrip("/")
    success_url = f"{base_url}/billing/success?order_id={order_id}"
    cancel_url = f"{base_url}/billing/cancel"
    
    checkout_result = kaspi_create_checkout(
        amount_minor=amount_minor,
        currency=currency,
        tenant_id=x_tenant_id,
        plan_id=legacy_plan_id,
        success_url=success_url,
        cancel_url=cancel_url,
        order_id=order_id
    )
    
    if not checkout_result:
        raise HTTPException(status_code=500, detail="Не удалось создать Kaspi checkout session")
    
    checkout_url = checkout_result.get("checkout_url")
    external_order_id = checkout_result.get("external_order_id")
    
    # Обновляем заказ с external_order_id
    if external_order_id:
        entitlement_svc.update_order_status(
            order_id=order_id,
            status="pending",
            external_order_id=external_order_id
        )

        # Создаем kaspi_orders row (tenant_id + plans.id + kaspi_order_id)
        try:
            from cyberplat.product.infrastructure.database import get_sessionmaker
            from cyberplat.product.infrastructure.kaspi_order_repository_sqlalchemy import KaspiOrderRepositoryImpl
            from sqlalchemy.orm import Session

            SessionLocal = get_sessionmaker()
            session: Session = SessionLocal()
            try:
                repo = KaspiOrderRepositoryImpl(session=session)
                # уникальность по kaspi_order_id
                existing = repo.get_by_kaspi_order_id(external_order_id)
                if not existing:
                    repo.create_order(tenant_id=x_tenant_id, plan_id=plan_id, kaspi_order_id=external_order_id)
            finally:
                session.close()
        except Exception as e:
            logger.warning(f"Failed to create kaspi_orders row: {e}")

        # Emit event for observability + outgoing webhooks
        try:
            evt = getattr(request.app.state, "event_service", None)
            if evt:
                evt.emit(
                    event_type="billing.kaspi.checkout.created",
                    tenant_id=x_tenant_id,
                    artifact_id=None,
                    payload={"plan_id": plan_id, "kaspi_order_id": external_order_id, "order_id": order_id},
                )
        except Exception as e:
            logger.warning(f"Failed to emit billing.kaspi.checkout.created: {e}")
    
    return KaspiCheckoutResponse(
        checkout_url=checkout_url,
        order_id=order_id,
        kaspi_order_id=external_order_id
    )


@app.post("/api/v1/billing/webhook/kaspi")
async def kaspi_webhook(request: Request):
    """
    Обработчик Kaspi webhook событий.
    
    Требует заголовок X-Kaspi-Signature для проверки подписи.
    Секрет берется из env: KASPI_WEBHOOK_SECRET
    
    ВАЖНО: Использует ProcessWebhookUseCase для обработки событий (Clean Architecture).
    """
    import os
    
    entitlement_svc = getattr(request.app.state, "entitlement_service", None)
    if not entitlement_svc:
        raise HTTPException(status_code=503, detail="Entitlement service not initialized")
    
    webhook_secret = os.getenv("KASPI_WEBHOOK_SECRET", "").strip()
    if not webhook_secret:
        logger.warning("KASPI_WEBHOOK_SECRET не установлен, webhook отключен")
        raise HTTPException(status_code=503, detail="Kaspi webhook secret not configured")
    
    # Читаем тело запроса
    body = await request.body()
    body_bytes = body
    body_str = body.decode('utf-8')
    
    # Проверка подписи
    signature = request.headers.get("X-Kaspi-Signature")
    if not signature:
        raise HTTPException(status_code=401, detail="Missing X-Kaspi-Signature header")
    
    try:
        payload = json.loads(body_str)
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {str(e)}")
    
    # Извлекаем event_id (Kaspi может использовать id или event_id)
    event_id = payload.get("id") or payload.get("event_id") or str(uuid.uuid4())

    # --- MVP commercial flow: kaspi_orders -> tenant_plans (idempotent) ---
    # We keep existing Clean Architecture webhook processing (tenant_subscriptions + billing_rates)
    # but additionally update new tenant_plans based on kaspi_orders table.
    def _extract_kaspi_order_id(p: dict) -> Optional[str]:
        for key in ("external_order_id", "kaspi_order_id", "order_id"):
            val = p.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
        data = p.get("data")
        if isinstance(data, dict):
            for key in ("external_order_id", "kaspi_order_id", "order_id"):
                val = data.get(key)
                if isinstance(val, str) and val.strip():
                    return val.strip()
        return None

    def _classify_event_type(t: str) -> Optional[str]:
        # paid / failed / canceled
        if t in ("payment.success", "payment.paid", "order.paid"):
            return "paid"
        if t in ("payment.failed", "order.failed"):
            return "failed"
        if t in ("payment.canceled", "order.canceled"):
            return "canceled"
        return None

    kaspi_event_type = payload.get("event_type") or payload.get("type") or "unknown"
    kaspi_order_id = _extract_kaspi_order_id(payload)
    kaspi_status_kind = _classify_event_type(kaspi_event_type)

    if kaspi_order_id and kaspi_status_kind in ("paid", "failed", "canceled"):
        try:
            from cyberplat.product.infrastructure.database import get_sessionmaker
            from cyberplat.product.infrastructure.kaspi_order_repository_sqlalchemy import KaspiOrderRepositoryImpl
            from cyberplat.product.infrastructure.plan_repositories_sqlalchemy import TenantPlanRepositoryImpl
            from sqlalchemy.orm import Session

            SessionLocal = get_sessionmaker()
            session: Session = SessionLocal()
            try:
                kaspi_repo = KaspiOrderRepositoryImpl(session=session)
                tenant_plan_repo = TenantPlanRepositoryImpl(session=session)

                order_row = kaspi_repo.get_by_kaspi_order_id(kaspi_order_id)
                if not order_row:
                    logger.warning(
                        f"Kaspi webhook for unknown kaspi_order_id={kaspi_order_id} "
                        f"(event_id={event_id}, type={kaspi_event_type})"
                    )
                else:
                    # Idempotency: already paid -> no-op
                    if kaspi_status_kind == "paid" and order_row.get("status") == "paid":
                        logger.info(f"Kaspi order already paid (idempotent): {kaspi_order_id}")
                    elif kaspi_status_kind == "paid":
                        # Mark paid + switch tenant_plans to paid plan
                        kaspi_repo.mark_paid(kaspi_order_id, payload=payload)
                        import os
                        from datetime import datetime, timedelta
                        period_days = int(os.getenv("BILLING_PERIOD_DAYS", "30"))
                        now = datetime.now()
                        expires_at = (now + timedelta(days=period_days)).isoformat()
                        tenant_plan_repo.assign_plan(
                            tenant_id=order_row["tenant_id"],
                            plan_id=order_row["plan_id"],
                            expires_at=expires_at,
                            subscription_status="active",
                            failed_charges=0,
                        )
                        # Emit event for outgoing webhooks + audit
                        evt = getattr(request.app.state, "event_service", None)
                        if evt:
                            evt.emit(
                                event_type="billing.plan.upgraded",
                                tenant_id=order_row["tenant_id"],
                                artifact_id=None,
                                payload={
                                    "plan_id": order_row["plan_id"],
                                    "provider": "kaspi",
                                    "kaspi_order_id": kaspi_order_id,
                                },
                            )
                    else:
                        # failed / canceled
                        kaspi_repo.mark_failed(
                            kaspi_order_id,
                            error=f"kaspi_{kaspi_status_kind}",
                            payload=payload,
                            status="failed" if kaspi_status_kind == "failed" else "canceled",
                        )
                        evt = getattr(request.app.state, "event_service", None)
                        if evt:
                            evt.emit(
                                event_type="billing.payment.failed",
                                tenant_id=order_row["tenant_id"],
                                artifact_id=None,
                                payload={
                                    "plan_id": order_row["plan_id"],
                                    "provider": "kaspi",
                                    "kaspi_order_id": kaspi_order_id,
                                    "reason": kaspi_status_kind,
                                },
                            )
            finally:
                session.close()
        except Exception as e:
            logger.warning(f"Failed to process kaspi_orders -> tenant_plans sync: {e}", exc_info=True)
    
    # Инициализируем Clean Architecture компоненты
    from cyberplat.billing.infrastructure.kaspi_provider import KaspiPaymentProvider
    from cyberplat.billing.infrastructure.repositories import (
        EntitlementSubscriptionRepository,
        EntitlementWebhookEventRepository
    )
    from cyberplat.billing.infrastructure.kaspi_webhook_handlers import create_kaspi_event_handlers
    from cyberplat.billing.application.process_webhook_use_case import ProcessWebhookUseCase
    
    # Получаем billing_service для сохранения Kaspi токенов
    billing_svc = getattr(request.app.state, "billing_service", None)
    
    # Создаем адаптеры
    provider = KaspiPaymentProvider()
    subscription_repo = EntitlementSubscriptionRepository(entitlement_svc, billing_service=billing_svc)
    event_repo = EntitlementWebhookEventRepository(entitlement_svc)
    
    # Создаем обработчики событий (передаём entitlement_service для lookup order)
    event_handlers = create_kaspi_event_handlers(entitlement_svc)
    
    # Создаем use case
    use_case = ProcessWebhookUseCase(
        provider=provider,
        event_repo=event_repo,
        subscription_repo=subscription_repo,
        event_handlers=event_handlers
    )
    
    # Извлекаем тип события для метрик (используем уже вычисленный)
    event_type = kaspi_event_type
    
    # Обрабатываем событие через use case
    try:
        success, tenant_id, error_msg = use_case.execute(
            event=payload,
            raw_payload=body_bytes,
            signature=signature,
            webhook_secret=webhook_secret
        )
        
        # Записываем метрику webhook события
        if metrics_enabled:
            if success:
                if tenant_id is None:
                    status = "duplicate"  # Идемпотентный ответ
                else:
                    status = "ok"
            else:
                if "not handled" in (error_msg or "") or "Missing" in (error_msg or "") or "Order not found" in (error_msg or ""):
                    status = "invalid"
                else:
                    status = "error"
            record_webhook_event("kaspi", event_type, status)
        
        # Формируем ответ в том же формате, что и раньше (для обратной совместимости)
        if success:
            # Если tenant_id None, это может быть идемпотентный ответ (событие уже обработано)
            if tenant_id is None:
                return {"status": "ok", "event_id": event_id, "message": "Already processed"}
            return {"status": "ok", "event_id": event_id, "tenant_id": tenant_id}
        else:
            # Событие проигнорировано или ошибка
            if error_msg:
                if "not handled" in error_msg or "Missing" in error_msg or "Order not found" in error_msg:
                    # Помечаем как ignored через event_repo
                    try:
                        event_repo.mark_event_ignored(
                            provider="kaspi",
                            event_id=event_id,
                            reason=error_msg
                        )
                    except Exception as e:
                        logger.warning(f"Failed to mark event as ignored: {e}")
                else:
                    # Ошибка обработки - помечаем как processed с ошибкой
                    try:
                        event_repo.mark_event_processed(
                            provider="kaspi",
                            event_id=event_id,
                            tenant_id=tenant_id
                        )
                        # Обновляем error_message если нужно
                        conn = entitlement_svc._get_connection()
                        cur = conn.cursor()
                        cur.execute("""
                            UPDATE billing_webhook_events
                            SET error_message = ?
                            WHERE event_id = ? AND provider = ?
                        """, (error_msg, event_id, "kaspi"))
                        conn.commit()
                        conn.close()
                    except Exception as e:
                        logger.warning(f"Failed to update event error: {e}")
            return {"status": "error", "event_id": event_id, "error": error_msg}
            
    except Exception as e:
        logger.error(f"Ошибка при обработке Kaspi webhook: {e}", exc_info=True)
        
        # Записываем метрику ошибки
        if metrics_enabled:
            event_type = payload.get("event_type") or payload.get("type") or "unknown"
            record_webhook_event("kaspi", event_type, "error")
        
        # Помечаем событие как processed с ошибкой
        try:
            event_repo.mark_event_processed(
                provider="kaspi",
                event_id=event_id,
                tenant_id=None
            )
            conn = entitlement_svc._get_connection()
            cur = conn.cursor()
            cur.execute("""
                UPDATE billing_webhook_events
                SET error_message = ?
                WHERE event_id = ? AND provider = ?
            """, (str(e), event_id, "kaspi"))
            conn.commit()
            conn.close()
        except Exception as update_error:
            logger.warning(f"Failed to update event error: {update_error}")
        raise HTTPException(status_code=500, detail=f"Webhook processing error: {str(e)}")


@app.post("/api/v1/billing/webhook/yoomoney")
async def yoomoney_webhook(request: Request):
    """
    Обработчик YooMoney webhook событий (каркас).
    
    Требует заголовок X-Webhook-Secret для проверки.
    Секрет берется из env: YOOMONEY_WEBHOOK_SECRET
    """
    import os
    
    entitlement_svc = getattr(request.app.state, "entitlement_service", None)
    if not entitlement_svc:
        raise HTTPException(status_code=503, detail="Entitlement service not initialized")
    
    webhook_secret = os.getenv("YOOMONEY_WEBHOOK_SECRET", "").strip()
    if not webhook_secret:
        logger.warning("YOOMONEY_WEBHOOK_SECRET не установлен, webhook отключен")
        raise HTTPException(status_code=503, detail="YooMoney webhook secret not configured")
    
    # Проверка секрета
    provided_secret = request.headers.get("X-Webhook-Secret")
    if not provided_secret or provided_secret != webhook_secret:
        raise HTTPException(status_code=401, detail="Invalid webhook secret")
    
    # Читаем тело запроса
    body = await request.body()
    body_str = body.decode('utf-8')
    
    try:
        payload = json.loads(body_str)
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {str(e)}")
    
    # TODO: implement signature verification per YooMoney spec
    event_id = payload.get("id") or payload.get("event_id") or str(uuid.uuid4())
    
    # Записываем событие
    webhook_id = entitlement_svc.record_webhook_event(
        provider="yoomoney",
        event_id=event_id,
        raw_json=body_str
    )
    
    # TODO: implement YooMoney event processing
    entitlement_svc.mark_webhook_ignored(webhook_id, "YooMoney webhook processing not implemented yet")
    
    return {"status": "received", "event_id": event_id, "message": "YooMoney webhook processing not implemented"}


@app.get("/api/v1/billing/invoice")
async def get_billing_invoice(
    request: Request,
    period: str,
    x_tenant_id: Optional[str] = Header(None, alias="X-Tenant-ID")
):
    """
    Получить invoice (счёт) за период с агрегацией по метрикам.
    
    - **X-Tenant-ID**: ID тенанта (обязателен)
    - **period**: Период в формате YYYY-MM (например, "2026-01")
    """
    bs = getattr(request.app.state, "billing_service", None)
    if not bs:
        raise HTTPException(status_code=500, detail="Billing not initialized")
    
    if not x_tenant_id:
        raise HTTPException(
            status_code=400,
            detail="X-Tenant-ID заголовок обязателен"
        )
    
    x_tenant_id = x_tenant_id.strip()
    if not x_tenant_id or x_tenant_id == "string":
        raise HTTPException(
            status_code=400,
            detail=f"Некорректный tenant_id: '{x_tenant_id}'"
        )
    
    # Валидация формата периода
    try:
        datetime.strptime(period, "%Y-%m")
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Некорректный формат периода: '{period}'. Ожидается YYYY-MM"
        )
    
    try:
        invoice = bs.get_invoice(tenant_id=x_tenant_id, period=period)
        return invoice
    except ValueError as e:
        # Ошибка с валютами
        logger.error(f"Ошибка при получении invoice: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=str(e)
        )


@app.get("/api/v1/billing/quota", response_model=QuotaResponse)
async def get_billing_quota(
    request: Request,
    period: str,
    x_tenant_id: Optional[str] = Header(None, alias="X-Tenant-ID")
):
    """
    Получить статус квот по всем метрикам для tenant за период.
    
    - **X-Tenant-ID**: ID тенанта (обязателен)
    - **period**: Период в формате YYYY-MM (например, "2026-01")
    """
    bs = getattr(request.app.state, "billing_service", None)
    if not bs:
        raise HTTPException(status_code=500, detail="Billing not initialized")
    
    if not x_tenant_id:
        raise HTTPException(
            status_code=400,
            detail="X-Tenant-ID заголовок обязателен"
        )
    
    x_tenant_id = x_tenant_id.strip()
    if not x_tenant_id or x_tenant_id == "string":
        raise HTTPException(
            status_code=400,
            detail=f"Некорректный tenant_id: '{x_tenant_id}'"
        )
    
    # Валидация формата периода
    try:
        datetime.strptime(period, "%Y-%m")
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Некорректный формат периода: '{period}'. Ожидается YYYY-MM"
        )
    
    try:
        quota_status = bs.get_quota_status(tenant_id=x_tenant_id, period=period)
        
        # Преобразуем metrics в MetricQuotaStatus объекты
        metrics_dict = {}
        for metric, status in quota_status["metrics"].items():
            metrics_dict[metric] = MetricQuotaStatus(**status)
        
        return QuotaResponse(
            tenant_id=quota_status["tenant_id"],
            period=quota_status["period"],
            currency=quota_status["currency"],
            metrics=metrics_dict
        )
    except ValueError as e:
        # Ошибка с валютами
        logger.error(f"Ошибка при получении quota status: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=str(e)
        )


@app.post("/api/v1/billing/admin/reset-usage")
async def admin_reset_usage(
    request: Request,
    reset_request: ResetUsageRequest,
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key")
):
    """
    Admin-only endpoint для сброса usage (для тестов/демо).
    
    - **X-Admin-Key**: Admin ключ (обязателен)
    - **tenant_id**: ID тенанта
    - **period**: Период в формате YYYY-MM
    """
    import os
    
    bs = getattr(request.app.state, "billing_service", None)
    if not bs:
        raise HTTPException(status_code=500, detail="Billing not initialized")
    
    # Проверка наличия admin key в ENV
    admin_key_env = os.getenv("BILLING_ADMIN_KEY", "").strip()
    if not admin_key_env:
        raise HTTPException(
            status_code=501,
            detail="Admin reset usage endpoint is not configured. Set BILLING_ADMIN_KEY environment variable."
        )
    
    # Проверка переданного ключа
    if not x_admin_key:
        raise HTTPException(
            status_code=403,
            detail="X-Admin-Key заголовок обязателен"
        )
    
    if x_admin_key != admin_key_env:
        raise HTTPException(
            status_code=403,
            detail="Invalid admin key"
        )
    
    # Валидация формата периода
    try:
        datetime.strptime(reset_request.period, "%Y-%m")
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Некорректный формат периода: '{reset_request.period}'. Ожидается YYYY-MM"
        )
    
    # Валидация tenant_id
    tenant_id = reset_request.tenant_id.strip()
    if not tenant_id or tenant_id == "string":
        raise HTTPException(
            status_code=400,
            detail=f"Некорректный tenant_id: '{reset_request.tenant_id}'"
        )
    
    # Выполняем сброс
    deleted_rows = bs.reset_usage(tenant_id=tenant_id, period=reset_request.period)
    
    return {
        "status": "ok",
        "deleted_rows": deleted_rows
    }


@app.get("/api/v1/billing/portal", response_model=BillingPortalResponse)
async def get_billing_portal(
    request: Request,
    period: Optional[str] = None,
    x_tenant_id: Optional[str] = Header(None, alias="X-Tenant-ID")
):
    """
    Получить Billing Portal данные (план, подписка, invoice, quota, upgrade/manage URLs).
    
    - **X-Tenant-ID**: ID тенанта (обязателен)
    - **period**: Период в формате YYYY-MM (опционально, по умолчанию текущий месяц)
    """
    from cyberplat.stripe_client import create_checkout_session, create_portal_session, get_price_id_for_plan
    
    bs = getattr(request.app.state, "billing_service", None)
    if not bs:
        raise HTTPException(status_code=500, detail="Billing not initialized")
    
    entitlement_svc = getattr(request.app.state, "entitlement_service", None)
    if not entitlement_svc:
        raise HTTPException(status_code=500, detail="Entitlement service not initialized")
    
    if not x_tenant_id:
        raise HTTPException(
            status_code=400,
            detail="X-Tenant-ID заголовок обязателен"
        )
    
    x_tenant_id = x_tenant_id.strip()
    if not x_tenant_id or x_tenant_id == "string":
        raise HTTPException(
            status_code=400,
            detail=f"Некорректный tenant_id: '{x_tenant_id}'"
        )
    
    # Определяем период (по умолчанию текущий месяц)
    if not period:
        period = datetime.now().strftime("%Y-%m")
    
    # Валидация формата периода
    try:
        datetime.strptime(period, "%Y-%m")
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Некорректный формат периода: '{period}'. Ожидается YYYY-MM"
        )
    
    # Получаем информацию о плане tenant (если используется система планов)
    tenant_plan_info = None
    trial_days_left = None
    upgrade_available = True
    
    try:
        from cyberplat.product.infrastructure.database import get_sessionmaker
        from cyberplat.product.infrastructure.plan_repositories_sqlalchemy import (
            TenantPlanRepositoryImpl,
            PlanRepositoryImpl
        )
        from sqlalchemy.orm import Session
        
        SessionLocal = get_sessionmaker()
        session: Session = SessionLocal()
        
        try:
            tenant_plan_repo = TenantPlanRepositoryImpl(session=session)
            plan_repo = PlanRepositoryImpl(session=session)
            
            tenant_plan = tenant_plan_repo.get_tenant_plan(x_tenant_id)
            if tenant_plan:
                plan = plan_repo.get_plan(tenant_plan["plan_id"])
                if plan:
                    tenant_plan_info = {
                        "plan_id": plan["id"],
                        "plan_name": plan["name"],
                        "plan_description": plan.get("description"),
                        "started_at": tenant_plan["started_at"],
                        "expires_at": tenant_plan.get("expires_at")
                    }
                    
                    # Вычисляем days_left для trial
                    if tenant_plan["plan_id"] == "trial" and tenant_plan.get("expires_at"):
                        try:
                            expires_at = datetime.fromisoformat(tenant_plan["expires_at"])
                            now = datetime.now()
                            if expires_at > now:
                                trial_days_left = (expires_at - now).days
                            else:
                                trial_days_left = 0
                        except Exception:
                            trial_days_left = None
                    
                    # Upgrade доступен если не enterprise
                    upgrade_available = plan["id"] != "enterprise"
        finally:
            session.close()
    except Exception as e:
        logger.warning(f"Failed to get tenant plan info: {e}")
    
    # Получаем информацию о плане и подписке (legacy, для совместимости)
    plan_info = entitlement_svc.get_tenant_plan_info(tenant_id=x_tenant_id)
    
    # Обновляем plan_info если есть информация о плане из новой системы
    if tenant_plan_info:
        plan_info["plan"]["id"] = tenant_plan_info["plan_id"]
        plan_info["plan"]["name"] = tenant_plan_info["plan_name"]
    
    # Получаем invoice
    try:
        invoice = bs.get_invoice(tenant_id=x_tenant_id, period=period)
    except ValueError as e:
        logger.error(f"Ошибка при получении invoice: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
    
    # Получаем quota status
    try:
        quota_status = bs.get_quota_status(tenant_id=x_tenant_id, period=period)
    except ValueError as e:
        logger.error(f"Ошибка при получении quota status: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

    # --- New plans/tenant_plans view (trial/pro/enterprise) for UI ---
    tenant_plan_info = None
    trial_days_left = None
    current_ui_plan_id = None  # trial|pro|enterprise
    current_subscription_status = None  # trial|active|past_due|canceled
    current_failed_charges = None
    current_expires_at = None
    current_days_left = None

    try:
        from cyberplat.product.infrastructure.database import get_sessionmaker
        from cyberplat.product.infrastructure.plan_repositories_sqlalchemy import (
            TenantPlanRepositoryImpl,
            PlanRepositoryImpl,
        )
        from cyberplat.product.application.assign_trial_use_case import AssignTrialUseCase
        from sqlalchemy.orm import Session

        SessionLocal = get_sessionmaker()
        session: Session = SessionLocal()
        try:
            tenant_plan_repo = TenantPlanRepositoryImpl(session=session)
            plan_repo = PlanRepositoryImpl(session=session)

            # auto-trial on first portal call
            AssignTrialUseCase(tenant_plan_repo=tenant_plan_repo, plan_repo=plan_repo).execute(x_tenant_id)

            tp = tenant_plan_repo.get_tenant_plan(x_tenant_id)
            if tp:
                current_ui_plan_id = tp["plan_id"]
                current_subscription_status = tp.get("subscription_status") or ("trial" if tp["plan_id"] == "trial" else "active")
                current_failed_charges = tp.get("failed_charges")
                current_expires_at = tp.get("expires_at")
                tenant_plan_info = tp

                if tp["plan_id"] == "trial" and tp.get("expires_at"):
                    try:
                        expires_at = datetime.fromisoformat(tp["expires_at"])
                        now = datetime.now()
                        trial_days_left = max(0, (expires_at - now).days)
                    except Exception:
                        trial_days_left = None
                elif tp.get("expires_at"):
                    try:
                        expires_at = datetime.fromisoformat(tp["expires_at"])
                        now = datetime.now()
                        current_days_left = max(0, (expires_at - now).days)
                    except Exception:
                        current_days_left = None
        finally:
            session.close()
    except Exception as e:
        logger.warning(f"Failed to read tenant_plans for portal: {e}")
    
    # Определяем upgrade_url и manage_url
    upgrade_url = None
    manage_url = None
    
    stripe_enabled = os.getenv("STRIPE_ENABLED", "0").strip() == "1"
    kaspi_enabled = os.getenv("KASPI_ENABLED", "0").strip() == "1"
    
    current_plan_id = plan_info["plan"]["id"]
    
    # Определяем целевой план для upgrade
    target_plan_id = None
    if current_plan_id == "plan_free":
        target_plan_id = "plan_pro"
    elif current_plan_id == "plan_pro":
        target_plan_id = "plan_enterprise"
    
    # Создаем upgrade_url если есть целевой план
    if target_plan_id:
        if stripe_enabled:
            # Используем Stripe checkout
            price_id = get_price_id_for_plan(target_plan_id)
            if price_id:
                # Получаем base URL из request
                base_url = str(request.base_url).rstrip("/")
                success_url = f"{base_url}/billing/success?session_id={{CHECKOUT_SESSION_ID}}"
                cancel_url = f"{base_url}/billing/cancel"
                
                checkout_session = create_checkout_session(
                    price_id=price_id,
                    tenant_id=x_tenant_id,
                    target_plan_id=target_plan_id,
                    success_url=success_url,
                    cancel_url=cancel_url
                )
                
                if checkout_session:
                    upgrade_url = checkout_session.get("url")
        
        elif kaspi_enabled:
            # Используем Kaspi checkout через наш endpoint
            # В реальности здесь можно напрямую вызывать kaspi_create_checkout,
            # но для консистентности используем тот же подход, что и для Stripe
            # (через создание заказа и получение checkout_url)
            try:
                # Получаем цену плана
                conn = entitlement_svc._get_connection()
                cur = conn.cursor()
                cur.execute("""
                    SELECT price_minor, currency
                    FROM billing_plans
                    WHERE id = ?
                """, (target_plan_id,))
                plan_row = cur.fetchone()
                conn.close()
                
                if plan_row:
                    # Создаем временный заказ для получения checkout_url
                    order_id = entitlement_svc.create_order(
                        tenant_id=x_tenant_id,
                        provider="kaspi",
                        plan_id=target_plan_id,
                        amount_minor=plan_row["price_minor"],
                        currency=plan_row["currency"]
                    )
                    
                    base_url = str(request.base_url).rstrip("/")
                    success_url = f"{base_url}/billing/success?order_id={order_id}"
                    cancel_url = f"{base_url}/billing/cancel"
                    
                    checkout_result = kaspi_create_checkout(
                        amount_minor=plan_row["price_minor"],
                        currency=plan_row["currency"],
                        tenant_id=x_tenant_id,
                        plan_id=target_plan_id,
                        success_url=success_url,
                        cancel_url=cancel_url,
                        order_id=order_id
                    )
                    
                    if checkout_result:
                        upgrade_url = checkout_result.get("checkout_url")
                        # Обновляем заказ с external_order_id
                        external_order_id = checkout_result.get("external_order_id")
                        if external_order_id:
                            entitlement_svc.update_order_status(
                                order_id=order_id,
                                status="pending",
                                external_order_id=external_order_id
                            )
            except Exception as e:
                logger.error(f"Ошибка при создании Kaspi checkout URL: {e}", exc_info=True)
                # Не падаем, просто не возвращаем upgrade_url
    
    # Создаем manage_url если есть customer_id (только для Stripe)
    if stripe_enabled:
        payment_profile = entitlement_svc.get_payment_profile(tenant_id=x_tenant_id)
        if payment_profile and payment_profile.get("stripe_customer_id"):
            customer_id = payment_profile["stripe_customer_id"]
            base_url = str(request.base_url).rstrip("/")
            return_url = f"{base_url}/billing/portal?period={period}"
            
            portal_session = create_portal_session(
                customer_id=customer_id,
                return_url=return_url
            )
            
            if portal_session:
                manage_url = portal_session.get("url")
    
    # Override plan/subscription for UI if tenant_plans is present
    plan_payload = plan_info["plan"]
    sub_payload = plan_info["subscription"]

    if current_ui_plan_id:
        plan_payload = {"id": current_ui_plan_id, "name": current_ui_plan_id.capitalize()}
        sub_payload = {
            "status": current_subscription_status or sub_payload.get("status"),
            "subscription_id": sub_payload.get("subscription_id"),
        }

    # Portal: for MVP, expose internal upgrade endpoint as upgrade_url (UI will POST to it)
    if kaspi_enabled and current_ui_plan_id in (None, "trial", "pro"):
        # UI chooses plan_id in body: pro/enterprise
        upgrade_url = "/api/v1/billing/checkout/kaspi"
    elif current_ui_plan_id == "enterprise":
        upgrade_url = None

    trial_info = None
    if current_ui_plan_id == "trial":
        trial_info = {"days_left": trial_days_left, "expires_at": tenant_plan_info.get("expires_at") if tenant_plan_info else None}

    return BillingPortalResponse(
        tenant_id=x_tenant_id,
        period=period,
        plan=PlanInfo(**plan_payload),
        subscription=SubscriptionInfo(**sub_payload),
        invoice=invoice,
        quota=quota_status,
        links=PortalLinks(upgrade_url=upgrade_url, manage_url=manage_url),
        trial=trial_info,
        upgrade_available=(current_ui_plan_id != "enterprise"),
        expires_at=current_expires_at,
        subscription_status=current_subscription_status,
        failed_charges=current_failed_charges,
        days_left=(trial_days_left if current_ui_plan_id == "trial" else current_days_left),
    )


@app.post("/api/v1/billing/cron/charge-kaspi", response_model=KaspiRecurringChargeResponse)
async def charge_kaspi_recurring(request: Request):
    """
    Запустить recurring charge для активных Kaspi подписок.
    
    Этот endpoint должен вызываться периодически (например, через cron) для автоматического
    продления подписок через Kaspi payment tokens.
    
    Требования:
    - BILLING_ENABLED=1
    - KASPI_ENABLED=1
    
    ВАЖНО: Использует RenewSubscriptionsUseCase для обработки recurring billing (Clean Architecture).
    
    Returns:
        {
            "status": "ok",
            "charged": int,  # Количество успешно списанных подписок
            "failed": int,    # Количество неудачных списаний
            "skipped": int,  # Количество пропущенных (период еще не закончился)
            "errors": List[str]  # Список ошибок
        }
    """
    import os
    
    # Проверка env переменных
    billing_enabled = os.getenv("BILLING_ENABLED", "0").strip() == "1"
    kaspi_enabled = os.getenv("KASPI_ENABLED", "0").strip() == "1"
    
    if not billing_enabled:
        raise HTTPException(
            status_code=404,
            detail="Billing is not enabled. Set BILLING_ENABLED=1 to enable."
        )
    
    if not kaspi_enabled:
        raise HTTPException(
            status_code=404,
            detail="Kaspi payment provider is not enabled. Set KASPI_ENABLED=1 to enable."
        )
    
    entitlement_svc = getattr(request.app.state, "entitlement_service", None)
    if not entitlement_svc:
        raise HTTPException(status_code=503, detail="Entitlement service not initialized")
    
    billing_svc = getattr(request.app.state, "billing_service", None)
    if not billing_svc:
        raise HTTPException(status_code=503, detail="Billing service not initialized")
    
    # New production-like recurring flow based on tenant_plans:
    # - pro/enterprise have expires_at + subscription_status + failed_charges
    # - renew window + idempotent extension: max(expires_at, now) + period_days
    import time
    start_time = time.time()
    import os
    from datetime import timedelta
    from cyberplat.kaspi_client import charge_token

    period_days = int(os.getenv("BILLING_PERIOD_DAYS", "30"))
    max_failed = int(os.getenv("SUBSCRIPTION_MAX_FAILED_CHARGES", "3"))
    renew_window_days = int(os.getenv("RENEW_WINDOW_DAYS", "2"))

    event_service_local = getattr(request.app.state, "event_service", None)

    from cyberplat.product.infrastructure.database import get_sessionmaker
    from cyberplat.product.infrastructure.plan_repositories_sqlalchemy import (
        TenantPlanRepositoryImpl,
        PlanRepositoryImpl,
    )
    from sqlalchemy.orm import Session

    now = datetime.now()
    renew_before = (now + timedelta(days=renew_window_days)).isoformat()

    charged = 0
    failed = 0
    skipped = 0
    errors = []

    SessionLocal = get_sessionmaker()
    session: Session = SessionLocal()
    try:
        tp_repo = TenantPlanRepositoryImpl(session=session)
        plan_repo = PlanRepositoryImpl(session=session)

        due = tp_repo.list_tenants_due_for_renewal(
            plan_ids=["pro", "enterprise"],
            statuses=["active", "past_due"],
            renew_before_iso=renew_before,
            limit=200,
        )

        for tp in due:
            tenant_id = tp["tenant_id"]
            plan_id = tp["plan_id"]
            expires_at = tp.get("expires_at")
            sub_status = tp.get("subscription_status") or ("active" if plan_id != "trial" else "trial")
            failed_charges = int(tp.get("failed_charges") or 0)

            # Load plan price/currency
            plan = plan_repo.get_plan(plan_id)
            if not plan or not plan.get("active"):
                skipped += 1
                errors.append(f"Tenant {tenant_id}: plan {plan_id} not found/active")
                continue
            amount_minor = plan.get("price_minor")
            currency = plan.get("currency") or "KZT"
            if amount_minor is None:
                skipped += 1
                continue

            # Get Kaspi token (payment method)
            token = billing_svc.get_kaspi_token(tenant_id)
            if not token:
                failed_charges += 1
                sub_status = "past_due"
                failed += 1
                err = f"Tenant {tenant_id}: missing kaspi_token"
                errors.append(err)
                logger.warning("kaspi_recurring_missing_token", extra={"tenant_id": tenant_id, "plan_id": plan_id})
            else:
                try:
                    legacy_plan_id = "plan_pro" if plan_id == "pro" else "plan_enterprise"
                    res = charge_token(
                        token=token,
                        amount_minor=amount_minor,
                        currency=currency,
                        tenant_id=tenant_id,
                        plan_id=legacy_plan_id,
                    )
                    if not res or res.get("status") != "success":
                        failed_charges += 1
                        sub_status = "past_due"
                        failed += 1
                        err = f"Tenant {tenant_id}: charge failed ({res})"
                        errors.append(err)
                        logger.warning(
                            "kaspi_recurring_charge_failed",
                            extra={"tenant_id": tenant_id, "plan_id": plan_id, "failed_charges": failed_charges},
                        )
                    else:
                        # extend expires_at idempotently
                        base_dt = now
                        if expires_at:
                            try:
                                exp_dt = datetime.fromisoformat(expires_at)
                                if exp_dt > base_dt:
                                    base_dt = exp_dt
                            except Exception:
                                pass
                        new_expires = (base_dt + timedelta(days=period_days)).isoformat()

                        tp_repo.update_subscription_state(
                            tenant_id=tenant_id,
                            expires_at=new_expires,
                            subscription_status="active",
                            failed_charges=0,
                        )
                        charged += 1
                        logger.info(
                            "kaspi_subscription_renewed",
                            extra={
                                "tenant_id": tenant_id,
                                "plan_id": plan_id,
                                "previous_expires_at": expires_at,
                                "new_expires_at": new_expires,
                            },
                        )
                        if event_service_local:
                            try:
                                event_service_local.emit(
                                    event_type="billing.subscription.renewed",
                                    tenant_id=tenant_id,
                                    artifact_id=None,
                                    payload={
                                        "plan_id": plan_id,
                                        "provider": "kaspi",
                                        "previous_expires_at": expires_at,
                                        "expires_at": new_expires,
                                    },
                                )
                            except Exception as e:
                                logger.warning(f"Failed to emit billing.subscription.renewed: {e}")
                        continue
                except Exception as e:
                    failed_charges += 1
                    sub_status = "past_due"
                    failed += 1
                    errors.append(f"Tenant {tenant_id}: exception charging: {e}")
                    logger.error("kaspi_recurring_exception", extra={"tenant_id": tenant_id, "plan_id": plan_id}, exc_info=True)

            # failure path: update state / downgrade if needed
            if failed_charges >= max_failed:
                # cancel + downgrade
                tp_repo.assign_plan(
                    tenant_id=tenant_id,
                    plan_id="trial",
                    expires_at=now.isoformat(),
                    subscription_status="canceled",
                    failed_charges=failed_charges,
                )
                logger.warning(
                    "kaspi_subscription_canceled",
                    extra={"tenant_id": tenant_id, "previous_plan_id": plan_id, "failed_charges": failed_charges},
                )
                if event_service_local:
                    try:
                        event_service_local.emit(
                            event_type="billing.subscription.canceled",
                            tenant_id=tenant_id,
                            artifact_id=None,
                            payload={
                                "plan_id": plan_id,
                                "provider": "kaspi",
                                "failed_charges": failed_charges,
                            },
                        )
                    except Exception as e:
                        logger.warning(f"Failed to emit billing.subscription.canceled: {e}")
            else:
                tp_repo.update_subscription_state(
                    tenant_id=tenant_id,
                    expires_at=expires_at,
                    subscription_status="past_due",
                    failed_charges=failed_charges,
                )
                if event_service_local:
                    try:
                        event_service_local.emit(
                            event_type="billing.subscription.past_due",
                            tenant_id=tenant_id,
                            artifact_id=None,
                            payload={
                                "plan_id": plan_id,
                                "provider": "kaspi",
                                "failed_charges": failed_charges,
                                "expires_at": expires_at,
                            },
                        )
                    except Exception as e:
                        logger.warning(f"Failed to emit billing.subscription.past_due: {e}")

    finally:
        session.close()

    duration = time.time() - start_time
    if metrics_enabled:
        status = "failed" if failed > 0 else ("success" if charged > 0 else "skipped")
        record_recurring_run("kaspi", status, duration)

    return {"status": "ok", "charged": charged, "failed": failed, "skipped": skipped, "errors": errors}


@app.get("/api/v1/export/status")
async def get_export_status():
    """
    Получить статус экспорта в S3.
    
    Returns:
        Информация о конфигурации S3 экспорта (без секретов)
    """
    global s3_exporter
    
    if not s3_exporter:
        return {
            "enabled": False,
            "bucket": None,
            "endpoint_url": None,
            "prefix": None
        }
    
    return {
        "enabled": True,
        "bucket": s3_exporter.bucket,
        "endpoint_url": s3_exporter.endpoint_url,
        "prefix": s3_exporter.prefix,
        "region": s3_exporter.region
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

