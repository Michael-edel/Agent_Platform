"""Фоновый воркер для обработки задач из очереди."""

import logging
import time
import threading
from pathlib import Path
from typing import Optional

from utils.logger import setup_logging
from utils.config import load_settings
from core.ai_client import AIClient
from storage.database import Database
from core.document_processor import DocumentProcessor
from storage.job_queue import JobQueue, JobStatus

logger = logging.getLogger(__name__)


class DocumentWorker:
    """Воркер для обработки документов из очереди."""
    
    def __init__(self, queue: JobQueue, stop_event: Optional[threading.Event] = None):
        self.queue = queue
        self.stop_event = stop_event or threading.Event()
        self.is_running = False
        self.worker_thread: Optional[threading.Thread] = None
        
        # Инициализация компонентов обработки
        settings = load_settings()
        self.ai_client = AIClient(
            api_key=settings.openai_api_key,
            model=settings.openai_model,
            max_concurrency=settings.max_openai_concurrency,
            min_interval_sec=settings.openai_min_interval_sec,
            max_retries=settings.openai_max_retries
        )
        self.db = Database(settings.db_path)
        self.processor = DocumentProcessor(ai=self.ai_client, db=self.db)
        self.settings = settings
    
    def _calculate_cost(self, tokens_in: int, tokens_out: int) -> float:
        """Рассчитать стоимость в USD."""
        cost_in = (tokens_in / 1000.0) * self.settings.price_per_1k_in
        cost_out = (tokens_out / 1000.0) * self.settings.price_per_1k_out
        return cost_in + cost_out
    
    def _process_job(self, job: dict) -> None:
        """Обработать одну задачу."""
        job_id = job["job_id"]
        file_path = Path(job["file_path"])
        
        logger.info(f"Начало обработки задачи {job_id}: {job['filename']}")
        
        try:
            # Обновляем статус на processing
            self.queue.update_job_status(job_id, JobStatus.PROCESSING, started_at=True)
            
            # Сбрасываем счетчики перед обработкой
            self.ai_client.reset_counters()
            self.processor.reset_stats()
            
            start_time = time.time()
            
            # Обрабатываем файл (process_file сам проверит кэш)
            result = self.processor.process_file(file_path)
            
            # Определяем, был ли использован кэш
            stats = self.processor.get_stats()
            cached = stats.get("cache_hit", 0) > 0
            
            if cached:
                logger.info(f"Cache hit for {job['filename']} (job_id: {job_id})")
            else:
                logger.info(f"Cache miss for {job['filename']} (job_id: {job_id})")
            
            # Получаем статистику
            elapsed_ms = int((time.time() - start_time) * 1000)
            processor_stats = self.processor.get_stats()
            openai_requests = self.ai_client.get_request_count()
            tokens_in, tokens_out = self.ai_client.get_tokens()
            
            # Рассчитываем стоимость
            cost_usd = self._calculate_cost(tokens_in, tokens_out)
            
            # Вычисляем хеш файла для сохранения в БД
            file_hash = self.processor.get_file_hash(file_path)
            
            # Сохраняем в базу статистики
            self.db.save_run(
                job_id=job_id,
                client_id=job.get("client_id"),
                filename=job["filename"],
                file_hash=file_hash,
                cached=cached,
                openai_requests=openai_requests,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                cost_usd=cost_usd,
                elapsed_ms=elapsed_ms
            )
            
            # Формируем ответ
            stats_dict = {
                "cache_hit": processor_stats.get("cache_hit", 0),
                "cache_miss": processor_stats.get("cache_miss", 0),
                "openai_requests": openai_requests,
                "elapsed_ms": elapsed_ms,
            }
            
            billing_dict = {
                "tokens_in": tokens_in,
                "tokens_out": tokens_out,
                "cost_usd": round(cost_usd, 6),
            }
            
            # Завершаем задачу успешно
            self.queue.complete_job(
                job_id=job_id,
                result=result,
                stats=stats_dict,
                billing=billing_dict,
                file_hash=file_hash
            )
            
            logger.info(f"Задача {job_id} обработана успешно за {elapsed_ms}ms")
            
            # Удаляем временный файл после успешной обработки
            if file_path.exists():
                try:
                    file_path.unlink()
                    logger.debug(f"Временный файл удален: {file_path}")
                except Exception as e:
                    logger.warning(f"Не удалось удалить временный файл {file_path}: {e}")
            
        except Exception as e:
            error_msg = f"{type(e).__name__}: {str(e)}"
            logger.error(f"Ошибка при обработке задачи {job_id}: {e}", exc_info=True)
            self.queue.fail_job(job_id, error_msg)
            
            # Удаляем временный файл даже при ошибке
            if file_path.exists():
                try:
                    file_path.unlink()
                    logger.debug(f"Временный файл удален после ошибки: {file_path}")
                except Exception:
                    pass
    
    def _worker_loop(self) -> None:
        """Основной цикл воркера."""
        logger.info("Воркер запущен")
        
        while not self.stop_event.is_set():
            try:
                # Получаем следующую задачу
                job = self.queue.get_next_job()
                
                if job:
                    # Обрабатываем задачу
                    self._process_job(job)
                else:
                    # Нет задач, ждем немного
                    time.sleep(1.0)
            
            except Exception as e:
                logger.error(f"Ошибка в цикле воркера: {e}", exc_info=True)
                time.sleep(5.0)  # Пауза при ошибке
        
        logger.info("Воркер остановлен")
    
    def start(self) -> None:
        """Запустить воркер в отдельном потоке."""
        if self.is_running:
            logger.warning("Воркер уже запущен")
            return
        
        self.is_running = True
        self.worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self.worker_thread.start()
        logger.info("Воркер запущен в фоновом режиме")
    
    def stop(self) -> None:
        """Остановить воркер."""
        if not self.is_running:
            return
        
        logger.info("Остановка воркера...")
        self.stop_event.set()
        
        if self.worker_thread:
            self.worker_thread.join(timeout=10.0)
        
        self.is_running = False
        logger.info("Воркер остановлен")


def run_worker(queue: JobQueue, stop_event: Optional[threading.Event] = None) -> DocumentWorker:
    """Создать и запустить воркер."""
    worker = DocumentWorker(queue, stop_event)
    worker.start()
    return worker

