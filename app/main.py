"""FastAPI приложение для обработки документов."""

import uuid
import logging
import threading
from pathlib import Path
from typing import Optional

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


@app.on_event("startup")
async def startup_event():
    """Инициализация при старте приложения."""
    global settings, db, queue, worker, stop_event
    
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
    
    logger.info("Приложение готово к работе (API + Worker)")


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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

