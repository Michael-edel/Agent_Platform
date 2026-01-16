"""Worker для обработки integration jobs."""

import logging
import time
import threading
from typing import Optional

from cyberplat.integrations.integration_job_service import IntegrationJobService
from cyberplat.integrations.onec_settings_service import OneCSettingsService
from cyberplat.integrations.onec_client import OneCClient, OneCAuthError, OneCTransportError, OneCResponseError
from cyberplat.integrations.onec_mapper import map_counterparty, map_contract, map_invoice, MappingValidationError
from cyberplat.integrations.idempotency_service import IdempotencyService
from cyberplat.case_service import CaseService

logger = logging.getLogger(__name__)

# Метрики (conditional import)
try:
    from cyberplat.observability.metrics import (
        onec_job_outcomes_total,
        onec_job_latency_seconds,
        onec_failures_total,
        METRICS_ENABLED
    )
    _metrics_available = METRICS_ENABLED
except Exception:
    _metrics_available = False
    onec_job_outcomes_total = None
    onec_job_latency_seconds = None
    onec_failures_total = None


class IntegrationWorker:
    """Воркер для обработки integration jobs."""
    
    def __init__(
        self,
        job_service: IntegrationJobService,
        settings_service: OneCSettingsService,
        idempotency_service: IdempotencyService,
        case_service: Optional[CaseService] = None,
        interval_seconds: float = 5.0,
        stop_event: Optional[threading.Event] = None
    ):
        """
        Инициализировать воркер.
        
        Args:
            job_service: Сервис для работы с jobs
            settings_service: Сервис для работы с настройками
            idempotency_service: Сервис для идемпотентности
            case_service: Сервис для работы с кейсами (опционально)
            interval_seconds: Интервал между итерациями
            stop_event: Событие для остановки воркера
        """
        self.job_service = job_service
        self.settings_service = settings_service
        self.idempotency_service = idempotency_service
        self.case_service = case_service
        self.interval_seconds = interval_seconds
        self.stop_event = stop_event or threading.Event()
        self.is_running = False
        self.worker_thread: Optional[threading.Thread] = None
    
    def _process_onec_job(self, job: dict) -> None:
        """Обработать один job для провайдера 1С."""
        job_id = job["id"]
        tenant_id = job["tenant_id"]
        job_type = job["job_type"]
        payload = job["payload"]
        case_id = job.get("case_id")
        
        start_time = time.time()
        
        try:
            # Получаем настройки tenant
            settings = self.settings_service.get_settings(tenant_id)
            if not settings:
                self.job_service.mark_failed(
                    job_id,
                    error_ru="Интеграция 1С не настроена для tenant",
                    error_code="integration_not_configured",
                    schedule_retry=False
                )
                return
            
            if not settings["enabled"]:
                self.job_service.mark_failed(
                    job_id,
                    error_ru="Интеграция 1С отключена для tenant",
                    error_code="integration_disabled",
                    schedule_retry=False
                )
                return
            
            # Создаём клиент 1С
            client = OneCClient(
                base_url=settings["base_url"],
                auth_type=settings["auth_type"],
                token=settings.get("token"),
                username=settings.get("username"),
                password=settings.get("password"),
                timeout=settings["timeout_seconds"]
            )
            
            # Формируем idempotency_key
            artifact_id = payload.get("artifact_id")
            idempotency_key = f"{job_type}:{artifact_id}" if artifact_id else f"{job_type}:{job_id}"
            
            # Проверяем идемпотентность
            object_type = job_type.replace("upsert_", "")
            remote_id = self.idempotency_service.get_remote_id(
                tenant_id=tenant_id,
                provider="onec",
                object_type=object_type,
                idempotency_key=idempotency_key
            )
            
            if remote_id:
                # Уже создано, пропускаем
                logger.info(f"Job {job_id}: объект уже создан в 1С (remote_id={remote_id})")
                self.job_service.mark_succeeded(job_id)
                return
            
            # Маппинг артефакта в payload 1С
            artifact = payload.get("artifact", {})
            
            if job_type == "upsert_counterparty":
                onec_payload = map_counterparty(artifact)
                response = client.create_counterparty(onec_payload, idempotency_key=idempotency_key)
            elif job_type == "upsert_contract":
                counterparty_id = payload.get("counterparty_id")
                onec_payload = map_contract(artifact, counterparty_id=counterparty_id)
                response = client.create_contract(onec_payload, idempotency_key=idempotency_key)
            elif job_type == "upsert_invoice":
                counterparty_id = payload.get("counterparty_id")
                contract_id = payload.get("contract_id")
                onec_payload = map_invoice(artifact, counterparty_id=counterparty_id, contract_id=contract_id)
                response = client.create_invoice(onec_payload, idempotency_key=idempotency_key)
            else:
                raise ValueError(f"Unknown job_type: {job_type}")
            
            # Получаем remote_id из ответа
            remote_id = response.get("id") or response.get("remote_id")
            if not remote_id:
                raise ValueError("1C API не вернул ID созданного объекта")
            
            # Отмечаем успех
            self.idempotency_service.mark_succeeded(
                tenant_id=tenant_id,
                provider="onec",
                object_type=object_type,
                idempotency_key=idempotency_key,
                remote_id=str(remote_id)
            )
            
            self.job_service.mark_succeeded(job_id)
            logger.info(f"Job {job_id}: успешно создан объект в 1С (remote_id={remote_id})")
            
            # Метрики успеха
            if _metrics_available and onec_job_outcomes_total:
                onec_job_outcomes_total.labels(status="succeeded", job_type=job_type).inc()
                if onec_job_latency_seconds:
                    latency = time.time() - start_time
                    onec_job_latency_seconds.labels(job_type=job_type).observe(latency)
            
        except MappingValidationError as e:
            # Ошибка валидации - не retry
            self.job_service.mark_failed(
                job_id,
                error_ru=e.message,
                error_code=e.error_code,
                schedule_retry=False
            )
            self._create_case_task_if_needed(case_id, tenant_id, e.message, e.error_code)
            
            # Метрики ошибки
            if _metrics_available:
                if onec_job_outcomes_total:
                    onec_job_outcomes_total.labels(status="failed", job_type=job_type).inc()
                if onec_failures_total:
                    onec_failures_total.labels(error_code=e.error_code).inc()
            
        except OneCAuthError as e:
            # Ошибка аутентификации - не retry
            self.job_service.mark_failed(
                job_id,
                error_ru="Ошибка аутентификации в 1С",
                error_code="onec_auth_error",
                schedule_retry=False
            )
            self._create_case_task_if_needed(case_id, tenant_id, "Ошибка аутентификации в 1С", "onec_auth_error")
            
            # Метрики ошибки
            if _metrics_available:
                if onec_job_outcomes_total:
                    onec_job_outcomes_total.labels(status="failed", job_type=job_type).inc()
                if onec_failures_total:
                    onec_failures_total.labels(error_code="onec_auth_error").inc()
            
        except (OneCTransportError, OneCResponseError) as e:
            # Ошибки транспорта/ответа - retry
            error_msg = str(e)
            if isinstance(e, OneCResponseError):
                error_code = f"onec_response_{e.status_code}"
            else:
                error_code = "onec_transport_error"
            
            self.job_service.mark_failed(
                job_id,
                error_ru=error_msg[:200],  # Ограничиваем длину
                error_code=error_code,
                schedule_retry=True
            )
            
        except Exception as e:
            # Неожиданная ошибка - retry
            logger.error(f"Job {job_id}: неожиданная ошибка: {e}", exc_info=True)
            error_code = "internal_error"
            self.job_service.mark_failed(
                job_id,
                error_ru=f"Внутренняя ошибка: {str(e)[:200]}",
                error_code=error_code,
                schedule_retry=True
            )
            
            # Метрики ошибки
            if _metrics_available:
                if onec_job_outcomes_total:
                    onec_job_outcomes_total.labels(status="retried", job_type=job_type).inc()
                if onec_failures_total:
                    onec_failures_total.labels(error_code=error_code).inc()
            
            # Метрики latency даже при ошибке
            if _metrics_available and onec_job_latency_seconds:
                latency = time.time() - start_time
                onec_job_latency_seconds.labels(job_type=job_type).observe(latency)
    
    def _create_case_task_if_needed(
        self,
        case_id: Optional[str],
        tenant_id: str,
        error_ru: str,
        error_code: str
    ) -> None:
        """Создать задачу в кейсе, если job окончательно failed и есть case_id."""
        if not case_id or not self.case_service:
            return
        
        try:
            # Проверяем, что кейс существует
            case = self.case_service.get_case(case_id, tenant_id=tenant_id)
            if not case:
                return
            
            # Создаём задачу
            self.case_service.add_task(
                case_id=case_id,
                step_key=case.get("current_step") or "error_handling",
                title=f"Исправить ошибку интеграции 1С: {error_ru[:100]}",
                assignee_role="tenant_admin"
            )
            
            # Логируем событие
            conn = self.case_service._get_connection()
            self.case_service._log_event(
                conn,
                case_id,
                "integration_error",
                {
                    "provider": "onec",
                    "error_code": error_code,
                    "error_ru": error_ru
                }
            )
            conn.commit()
            conn.close()
            
            logger.info(f"Создана задача в кейсе {case_id} для ошибки интеграции 1С")
        except Exception as e:
            logger.error(f"Ошибка при создании задачи в кейсе: {e}", exc_info=True)
    
    def _worker_loop(self) -> None:
        """Основной цикл воркера."""
        logger.info("Integration worker запущен")
        
        while not self.stop_event.is_set():
            try:
                # Забираем jobs для обработки
                jobs = self.job_service.claim_for_processing(limit=50)
                
                for job in jobs:
                    if self.stop_event.is_set():
                        break
                    
                    provider = job["provider"]
                    if provider == "onec":
                        self._process_onec_job(job)
                    else:
                        logger.warning(f"Unknown provider: {provider}")
                
                # Пауза между итерациями
                if not jobs:
                    time.sleep(self.interval_seconds)
                else:
                    # Если были jobs, небольшая пауза
                    time.sleep(0.5)
                    
            except Exception as e:
                logger.error(f"Ошибка в цикле integration worker: {e}", exc_info=True)
                time.sleep(5.0)
        
        logger.info("Integration worker остановлен")
    
    def start(self) -> None:
        """Запустить воркер в отдельном потоке."""
        if self.is_running:
            logger.warning("Integration worker уже запущен")
            return
        
        self.is_running = True
        self.worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self.worker_thread.start()
        logger.info("Integration worker запущен в отдельном потоке")
    
    def stop(self) -> None:
        """Остановить воркер."""
        if not self.is_running:
            return
        
        self.stop_event.set()
        if self.worker_thread:
            self.worker_thread.join(timeout=10.0)
        self.is_running = False
        logger.info("Integration worker остановлен")
