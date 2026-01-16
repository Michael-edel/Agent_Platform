"""Hook для автоматической постановки 1С jobs из Artifact pipeline."""

import logging
from typing import Optional, Dict, Any

from cyberplat.integrations.onec_settings_service import OneCSettingsService
from cyberplat.integrations.integration_job_service import IntegrationJobService
from cyberplat.integrations.idempotency_service import IdempotencyService
from cyberplat.case_service import CaseService
from cyberplat.artifact_service import ArtifactService

logger = logging.getLogger(__name__)

# Метрики (conditional import)
try:
    from cyberplat.observability.metrics import (
        onec_auto_jobs_total,
        onec_artifact_hook_errors_total,
        METRICS_ENABLED
    )
    _metrics_available = METRICS_ENABLED
except Exception:
    _metrics_available = False
    onec_auto_jobs_total = None
    onec_artifact_hook_errors_total = None


class OneCArtifactHook:
    """Подписчик на события артефактов для автоматической постановки 1С jobs."""
    
    def __init__(
        self,
        settings_service: OneCSettingsService,
        job_service: IntegrationJobService,
        idempotency_service: IdempotencyService,
        artifact_service: ArtifactService,
        case_service: Optional[CaseService] = None
    ):
        """
        Инициализировать hook.
        
        Args:
            settings_service: Сервис настроек 1С
            job_service: Сервис jobs интеграций
            idempotency_service: Сервис идемпотентности
            artifact_service: Сервис артефактов (для получения данных)
            case_service: Сервис кейсов (опционально, для audit events)
        """
        self.settings_service = settings_service
        self.job_service = job_service
        self.idempotency_service = idempotency_service
        self.artifact_service = artifact_service
        self.case_service = case_service
    
    def __call__(
        self,
        event_id: str,
        event_type: str,
        tenant_id: str,
        artifact_id: Optional[str],
        payload: Optional[Dict[str, Any]],
        created_at: str
    ):
        """
        Обработать событие артефакта.
        
        Args:
            event_id: ID события
            event_type: Тип события
            tenant_id: ID тенанта
            artifact_id: ID артефакта
            payload: Дополнительные данные события
            created_at: Время создания события
        """
        # Обрабатываем только события артефактов
        if event_type not in {"artifact.created", "artifact.finalized"}:
            return
        
        if not artifact_id:
            return
        
        try:
            self.handle_artifact_finalized(
                artifact_id=artifact_id,
                tenant_id=tenant_id,
                case_id=payload.get("case_id") if payload else None,
                event_id=event_id
            )
        except Exception as e:
            logger.error(f"Ошибка в OneC artifact hook для {artifact_id}: {e}", exc_info=True)
            
            # Метрики ошибок
            if _metrics_available and onec_artifact_hook_errors_total:
                onec_artifact_hook_errors_total.labels(error_code="hook_error").inc()
    
    def handle_artifact_finalized(
        self,
        artifact_id: str,
        tenant_id: str,
        case_id: Optional[str] = None,
        event_id: Optional[str] = None
    ) -> Optional[str]:
        """
        Обработать финализированный артефакт и поставить job в очередь 1С.
        
        Args:
            artifact_id: ID артефакта
            tenant_id: ID тенанта
            case_id: ID кейса (опционально)
            event_id: ID события (для correlation)
            
        Returns:
            ID созданного job или None если job не был создан
        """
        # Получаем артефакт (нужен artifact_service, но для MVP используем упрощённый подход)
        # В реальности нужно получить artifact через ArtifactService
        # Для MVP предполагаем, что artifact доступен через payload или нужно получить отдельно
        
        # Проверяем настройки tenant
        settings = self.settings_service.get_settings(tenant_id)
        if not settings:
            # Интеграция не настроена - silent skip
            logger.debug(f"1С интеграция не настроена для tenant {tenant_id}, пропускаем артефакт {artifact_id}")
            return None
        
        if not settings["enabled"]:
            # Интеграция отключена - silent skip
            logger.debug(f"1С интеграция отключена для tenant {tenant_id}, пропускаем артефакт {artifact_id}")
            return None
        
        # Получаем артефакт для определения типа
        artifact = self.artifact_service.get_artifact(artifact_id)
        if not artifact:
            logger.warning(f"Артефакт {artifact_id} не найден, пропускаем")
            return None
        
        artifact_kind = artifact.get("kind")
        if not artifact_kind:
            logger.debug(f"Артефакт {artifact_id} не имеет kind, пропускаем")
            return None
        
        # Маппинг artifact.kind -> job_type
        job_type_map = {
            "counterparty": "upsert_counterparty",
            "contract": "upsert_contract",
            "invoice": "upsert_invoice"
        }
        
        job_type = job_type_map.get(artifact_kind)
        if not job_type:
            # Неподдерживаемый тип артефакта - silent skip
            logger.debug(f"Неподдерживаемый тип артефакта: {artifact_kind}, пропускаем")
            return None
        
        # Формируем idempotency_key
        idempotency_key = f"onec:{tenant_id}:{artifact_kind}:{artifact_id}"
        
        # Проверяем идемпотентность (если уже есть succeeded job - пропускаем)
        object_type = artifact_kind
        remote_id = self.idempotency_service.get_remote_id(
            tenant_id=tenant_id,
            provider="onec",
            object_type=object_type,
            idempotency_key=idempotency_key
        )
        
        if remote_id:
            # Уже создано - пропускаем
            logger.debug(f"Артефакт {artifact_id} уже синхронизирован с 1С (remote_id={remote_id}), пропускаем")
            return None
        
        # Проверяем, нет ли уже pending/processing job для этого артефакта
        existing_jobs = self.job_service.list_jobs(
            tenant_id=tenant_id,
            provider="onec",
            status="pending"
        )
        
        # Также проверяем processing jobs
        processing_jobs = self.job_service.list_jobs(
            tenant_id=tenant_id,
            provider="onec",
            status="processing"
        )
        existing_jobs.extend(processing_jobs)
        
        # Проверяем по job_type и artifact_id в payload
        for job in existing_jobs:
            if job.get("job_type") == job_type:
                # Получаем полный job для проверки payload
                full_job = self.job_service.get_job(job["id"])
                if full_job and full_job.get("payload", {}).get("artifact_id") == artifact_id:
                    logger.debug(f"Job для артефакта {artifact_id} уже в очереди (job_id={job['id']}), пропускаем")
                    return job["id"]
        
        # Формируем payload с артефактом
        payload = {
            "artifact_id": artifact_id,
            "artifact": artifact  # Передаём полный артефакт для worker
        }
        
        # Создаём job
        job_id = self.job_service.enqueue(
            tenant_id=tenant_id,
            provider="onec",
            job_type=job_type,
            payload=payload,
            case_id=case_id
        )
        
        logger.info(f"Создан 1С job для артефакта {artifact_id} (job_id={job_id}, job_type={job_type}, tenant={tenant_id})")
        
        # Метрики
        if _metrics_available and onec_auto_jobs_total:
            onec_auto_jobs_total.labels(object_type=artifact_kind).inc()
        
        # Записываем audit event в кейс (если есть case_id и case_service)
        if case_id and self.case_service:
            try:
                conn = self.case_service._get_connection()
                self.case_service._log_event(
                    conn,
                    case_id,
                    "integration_job_queued",
                    {
                        "provider": "onec",
                        "job_id": job_id,
                        "artifact_id": artifact_id,
                        "job_type": job_type
                    }
                )
                conn.commit()
                conn.close()
            except Exception as e:
                logger.warning(f"Ошибка при записи audit event в кейс {case_id}: {e}")
        
        return job_id


def create_onec_artifact_hook(
    settings_service: OneCSettingsService,
    job_service: IntegrationJobService,
    idempotency_service: IdempotencyService,
    artifact_service: ArtifactService,
    case_service: Optional[CaseService] = None
) -> OneCArtifactHook:
    """Создать и вернуть hook для подписки на события."""
    return OneCArtifactHook(
        settings_service=settings_service,
        job_service=job_service,
        idempotency_service=idempotency_service,
        artifact_service=artifact_service,
        case_service=case_service
    )
