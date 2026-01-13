"""FastAPI приложение для обработки документов."""

import uuid
import logging
import threading
from pathlib import Path
from typing import Optional, Dict, Any

from fastapi import FastAPI, UploadFile, File, HTTPException, Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import sys
from pathlib import Path as PathLib

# Добавляем корневую директорию в путь для импортов
root_dir = PathLib(__file__).parent.parent
sys.path.insert(0, str(root_dir))

from utils.logger import setup_logging
from utils.config import load_settings, Settings
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

# Настройка логирования
setup_logging("INFO")
logger = logging.getLogger(__name__)

# Инициализация приложения
app = FastAPI(
    title="Document Processing API",
    description="API для обработки PDF счетов и других документов",
    version="1.0.0"
)

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


@app.on_event("startup")
async def startup_event():
    """Инициализация при старте приложения."""
    global settings, db, queue, worker, stop_event
    global artifact_service, event_service, storage_service, agent_registry
    
    logger.info("Инициализация приложения...")
    settings = load_settings()
    
    # Инициализация базы данных
    db = Database(settings.db_path)
    
    # Инициализация очереди задач
    queue = JobQueue()
    
    # Запуск воркера
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
    
    logger.info("Приложение готово к работе (API + Worker + Agent Platform)")


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
async def health():
    """Проверка здоровья сервиса."""
    return {
        "status": "ok",
        "service": "document-processing-api",
        "worker_running": worker.is_running if worker else False
    }


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
            tenant_id=x_tenant_id
        )
        
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
    except Exception as e:
        logger.error(f"Ошибка при загрузке документа: {e}", exc_info=True)
        if temp_file.exists():
            temp_file.unlink()
        raise HTTPException(status_code=500, detail=f"Ошибка при загрузке документа: {str(e)}")


@app.post("/agents/doc_agent/run", response_model=AgentRunResponse)
async def run_doc_agent(
    request: AgentRunRequest,
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
    tenant_id = request.tenant_id or x_tenant_id
    
    # Если tenant_id передан - нормализуем
    if tenant_id:
        tenant_id = tenant_id.strip()
        if not tenant_id or tenant_id == "string":
            raise HTTPException(
                status_code=400,
                detail=f"Некорректный tenant_id: '{tenant_id}'. tenant_id не может быть пустым или 'string'"
            )
    
    # Определяем file_id (приоритет: из запроса > artifact_id)
    file_id = request.file_id or request.artifact_id
    
    # Создаем контекст
    context = AgentContext(
        artifact_id=request.artifact_id,
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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

