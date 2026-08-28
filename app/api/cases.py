"""API роутер для работы с кейсами."""

import logging
from typing import Optional, List
from fastapi import APIRouter, HTTPException, Header, Depends
from pydantic import BaseModel

from cyberplat.case_service import CaseService, CaseNotFoundError, InvalidTransitionError
from cyberplat.artifact_service import ArtifactService
from cyberplat.integrations.onec_settings_service import OneCSettingsService
from cyberplat.integrations.integration_job_service import IntegrationJobService
from cyberplat.integrations.idempotency_service import IdempotencyService
from app.security.auth import require_roles

logger = logging.getLogger(__name__)

router = APIRouter()


# Pydantic модели для запросов и ответов
class CreateCaseRequest(BaseModel):
    case_type: str
    title: str
    initial_step: Optional[str] = None


class CaseResponse(BaseModel):
    id: str
    tenant_id: str
    case_type: str
    status: str
    current_step: Optional[str]
    title: str
    created_at: str
    updated_at: str


class CasesListResponse(BaseModel):
    items: List[CaseResponse]
    total: int


class AddTaskRequest(BaseModel):
    step_key: str
    title: str
    assignee_role: Optional[str] = None
    due_at: Optional[str] = None


class TaskResponse(BaseModel):
    id: str
    case_id: str
    step_key: str
    title: str
    assignee_role: Optional[str]
    status: str
    due_at: Optional[str]
    created_at: str
    completed_at: Optional[str]


class CompleteTaskRequest(BaseModel):
    pass  # Пустой запрос, task_id в пути


class TransitionStepRequest(BaseModel):
    new_step: str
    from_step: Optional[str] = None


class ErrorResponse(BaseModel):
    error: str
    error_code: str
    message: Optional[str] = None


class SyncOneCRequest(BaseModel):
    object_type: str  # counterparty, contract, invoice
    artifact_id: str


class SyncOneCResponse(BaseModel):
    success: bool
    message: str
    job_id: Optional[str] = None
    remote_id: Optional[str] = None


# Dependency для получения CaseService
def get_case_service() -> CaseService:
    """Получить экземпляр CaseService."""
    return CaseService()


def _require_tenant_case(case_service: CaseService, case_id: str, tenant_id: str) -> None:
    """Raise 404 unless the case belongs to the tenant in the request."""
    if not case_service.get_case(case_id, tenant_id=tenant_id):
        raise HTTPException(status_code=404, detail="Кейс не найден")


def get_artifact_service() -> ArtifactService:
    """Получить экземпляр ArtifactService."""
    return ArtifactService()


def get_onec_settings_service() -> OneCSettingsService:
    """Получить экземпляр OneCSettingsService."""
    return OneCSettingsService()


def get_integration_job_service() -> IntegrationJobService:
    """Получить экземпляр IntegrationJobService."""
    return IntegrationJobService()


def get_idempotency_service() -> IdempotencyService:
    """Получить экземпляр IdempotencyService."""
    return IdempotencyService()


@router.post("/cases", response_model=CaseResponse, status_code=201)
async def create_case(
    request: CreateCaseRequest,
    x_tenant_id: Optional[str] = Header(None, alias="X-Tenant-ID"),
    case_service: CaseService = Depends(get_case_service),
    _: None = Depends(require_roles("accountant", "system")),
):
    """
    Создать новый кейс.
    
    Требует заголовок X-Tenant-ID.
    """
    if not x_tenant_id:
        raise HTTPException(status_code=400, detail="X-Tenant-ID header обязателен")
    
    try:
        case_id = case_service.create_case(
            tenant_id=x_tenant_id,
            case_type=request.case_type,
            title=request.title,
            initial_step=request.initial_step
        )
        
        case = case_service.get_case(case_id, tenant_id=x_tenant_id)
        if not case:
            raise HTTPException(status_code=500, detail="Ошибка при создании кейса")
        
        return CaseResponse(**case)
    except Exception as e:
        logger.error(f"Ошибка при создании кейса: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Ошибка при создании кейса: {str(e)}")


@router.get("/cases/{case_id}", response_model=CaseResponse)
async def get_case(
    case_id: str,
    x_tenant_id: Optional[str] = Header(None, alias="X-Tenant-ID"),
    case_service: CaseService = Depends(get_case_service)
):
    """
    Получить кейс по ID.
    
    Требует заголовок X-Tenant-ID.
    """
    if not x_tenant_id:
        raise HTTPException(status_code=400, detail="X-Tenant-ID header обязателен")
    
    try:
        case = case_service.get_case(case_id, tenant_id=x_tenant_id)
        if not case:
            raise HTTPException(status_code=404, detail="Кейс не найден")
        return CaseResponse(**case)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка при получении кейса: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Ошибка при получении кейса: {str(e)}")


@router.get("/cases", response_model=CasesListResponse)
async def list_cases(
    tenant_id: Optional[str] = None,
    status: Optional[str] = None,
    case_type: Optional[str] = None,
    x_tenant_id: Optional[str] = Header(None, alias="X-Tenant-ID"),
    case_service: CaseService = Depends(get_case_service)
):
    """
    Получить список кейсов.
    
    Требует заголовок X-Tenant-ID или параметр tenant_id.
    """
    # Используем tenant_id из параметра или заголовка
    effective_tenant_id = tenant_id or x_tenant_id
    if not effective_tenant_id:
        raise HTTPException(status_code=400, detail="X-Tenant-ID header или параметр tenant_id обязателен")
    
    try:
        cases = case_service.list_cases(
            tenant_id=effective_tenant_id,
            status=status,
            case_type=case_type
        )
        return CasesListResponse(
            items=[CaseResponse(**case) for case in cases],
            total=len(cases)
        )
    except Exception as e:
        logger.error(f"Ошибка при получении списка кейсов: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Ошибка при получении списка кейсов: {str(e)}")


@router.post("/cases/{case_id}/tasks", response_model=TaskResponse, status_code=201)
async def add_task(
    case_id: str,
    request: AddTaskRequest,
    x_tenant_id: Optional[str] = Header(None, alias="X-Tenant-ID"),
    case_service: CaseService = Depends(get_case_service),
    _: None = Depends(require_roles("accountant", "system")),
):
    """
    Добавить задачу к кейсу.
    
    Требует заголовок X-Tenant-ID.
    """
    if not x_tenant_id:
        raise HTTPException(status_code=400, detail="X-Tenant-ID header обязателен")
    
    try:
        _require_tenant_case(case_service, case_id, x_tenant_id)
        task_id = case_service.add_task(
            case_id=case_id,
            step_key=request.step_key,
            title=request.title,
            assignee_role=request.assignee_role,
            due_at=request.due_at
        )
        
        # Получаем задачу для ответа
        task = case_service.get_task(task_id, case_id=case_id)
        if not task:
            raise HTTPException(status_code=500, detail="Ошибка при создании задачи")
        
        return TaskResponse(**task)
    except HTTPException:
        raise
    except CaseNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Ошибка при добавлении задачи: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Ошибка при добавлении задачи: {str(e)}")


@router.post("/cases/{case_id}/tasks/{task_id}/complete", status_code=200)
async def complete_task(
    case_id: str,
    task_id: str,
    x_tenant_id: Optional[str] = Header(None, alias="X-Tenant-ID"),
    case_service: CaseService = Depends(get_case_service),
    _: None = Depends(require_roles("accountant", "system")),
):
    """
    Завершить задачу.
    
    Требует заголовок X-Tenant-ID.
    """
    if not x_tenant_id:
        raise HTTPException(status_code=400, detail="X-Tenant-ID header обязателен")
    
    try:
        _require_tenant_case(case_service, case_id, x_tenant_id)
        case_service.complete_task(case_id, task_id)
        return {"status": "ok", "message": "Задача завершена"}
    except HTTPException:
        raise
    except CaseNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Ошибка при завершении задачи: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Ошибка при завершении задачи: {str(e)}")


@router.post("/cases/{case_id}/transition", status_code=200)
async def transition_step(
    case_id: str,
    request: TransitionStepRequest,
    x_tenant_id: Optional[str] = Header(None, alias="X-Tenant-ID"),
    case_service: CaseService = Depends(get_case_service),
    _: None = Depends(require_roles("accountant", "system")),
):
    """
    Перевести кейс на новый шаг.
    
    Требует заголовок X-Tenant-ID.
    """
    if not x_tenant_id:
        raise HTTPException(status_code=400, detail="X-Tenant-ID header обязателен")
    
    try:
        _require_tenant_case(case_service, case_id, x_tenant_id)
        case_service.transition_step(
            case_id=case_id,
            new_step=request.new_step,
            from_step=request.from_step
        )
        return {"status": "ok", "message": "Кейс переведён на новый шаг"}
    except HTTPException:
        raise
    except CaseNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except InvalidTransitionError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Ошибка при переходе шага: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Ошибка при переходе шага: {str(e)}")


@router.post("/cases/{case_id}/close", status_code=200)
async def close_case(
    case_id: str,
    x_tenant_id: Optional[str] = Header(None, alias="X-Tenant-ID"),
    case_service: CaseService = Depends(get_case_service),
    _: None = Depends(require_roles("accountant", "system")),
):
    """
    Закрыть кейс.
    
    Требует заголовок X-Tenant-ID.
    """
    if not x_tenant_id:
        raise HTTPException(status_code=400, detail="X-Tenant-ID header обязателен")
    
    try:
        _require_tenant_case(case_service, case_id, x_tenant_id)
        case_service.close_case(case_id)
        return {"status": "ok", "message": "Кейс закрыт"}
    except HTTPException:
        raise
    except CaseNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Ошибка при закрытии кейса: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Ошибка при закрытии кейса: {str(e)}")


@router.post("/cases/{case_id}/sync/onec", response_model=SyncOneCResponse, status_code=200)
async def sync_case_to_onec(
    case_id: str,
    request: SyncOneCRequest,
    x_tenant_id: Optional[str] = Header(None, alias="X-Tenant-ID"),
    case_service: CaseService = Depends(get_case_service),
    artifact_service: ArtifactService = Depends(get_artifact_service),
    settings_service: OneCSettingsService = Depends(get_onec_settings_service),
    job_service: IntegrationJobService = Depends(get_integration_job_service),
    idempotency_service: IdempotencyService = Depends(get_idempotency_service),
    _: None = Depends(require_roles("system")),
):
    """
    Ручная синхронизация артефакта из кейса в 1С.
    
    Требует заголовок X-Tenant-ID.
    """
    if not x_tenant_id:
        raise HTTPException(status_code=400, detail="X-Tenant-ID header обязателен")
    
    try:
        # Проверяем, что кейс существует и принадлежит tenant
        case = case_service.get_case(case_id, tenant_id=x_tenant_id)
        if not case:
            raise HTTPException(status_code=404, detail="Кейс не найден")
        
        # Проверяем, что артефакт существует и принадлежит tenant
        artifact = artifact_service.get_artifact(request.artifact_id)
        if not artifact:
            raise HTTPException(status_code=404, detail="Артефакт не найден")
        
        if artifact.get("tenant_id") != x_tenant_id:
            raise HTTPException(status_code=403, detail="Артефакт не принадлежит этому tenant")
        
        # Проверяем настройки 1С
        settings = settings_service.get_settings(x_tenant_id)
        if not settings:
            return SyncOneCResponse(
                success=False,
                message="Интеграция 1С не настроена для tenant"
            )
        
        if not settings["enabled"]:
            return SyncOneCResponse(
                success=False,
                message="Интеграция 1С отключена для tenant"
            )
        
        # Проверяем, что artifact.kind соответствует object_type
        artifact_kind = artifact.get("kind")
        if artifact_kind != request.object_type:
            raise HTTPException(
                status_code=400,
                detail=f"Тип артефакта ({artifact_kind}) не соответствует запрошенному ({request.object_type})"
            )
        
        # Маппинг object_type -> job_type
        job_type_map = {
            "counterparty": "upsert_counterparty",
            "contract": "upsert_contract",
            "invoice": "upsert_invoice"
        }
        
        job_type = job_type_map.get(request.object_type)
        if not job_type:
            raise HTTPException(
                status_code=400,
                detail=f"Неподдерживаемый тип объекта: {request.object_type}"
            )
        
        # Формируем idempotency_key
        idempotency_key = f"onec:{x_tenant_id}:{request.object_type}:{request.artifact_id}"
        
        # Проверяем идемпотентность (если уже есть succeeded - возвращаем remote_id)
        remote_id = idempotency_service.get_remote_id(
            tenant_id=x_tenant_id,
            provider="onec",
            object_type=request.object_type,
            idempotency_key=idempotency_key
        )
        
        if remote_id:
            return SyncOneCResponse(
                success=True,
                message="Артефакт уже синхронизирован с 1С",
                remote_id=remote_id
            )
        
        # Проверяем, нет ли уже pending/processing job
        existing_jobs = job_service.list_jobs(
            tenant_id=x_tenant_id,
            provider="onec",
            status="pending"
        )
        processing_jobs = job_service.list_jobs(
            tenant_id=x_tenant_id,
            provider="onec",
            status="processing"
        )
        existing_jobs.extend(processing_jobs)
        
        for job in existing_jobs:
            if job.get("job_type") == job_type:
                full_job = job_service.get_job(job["id"])
                if full_job and full_job.get("payload", {}).get("artifact_id") == request.artifact_id:
                    return SyncOneCResponse(
                        success=True,
                        message="Задача уже в очереди 1С",
                        job_id=job["id"]
                    )
        
        # Создаём job
        payload = {
            "artifact_id": request.artifact_id,
            "artifact": artifact
        }
        
        job_id = job_service.enqueue(
            tenant_id=x_tenant_id,
            provider="onec",
            job_type=job_type,
            payload=payload,
            case_id=case_id
        )
        
        # Метрики
        try:
            from cyberplat.observability.metrics import onec_manual_jobs_total, METRICS_ENABLED
            if METRICS_ENABLED and onec_manual_jobs_total:
                onec_manual_jobs_total.labels(object_type=request.object_type).inc()
        except Exception:
            pass
        
        # Записываем audit event в кейс
        try:
            conn = case_service._get_connection()
            case_service._log_event(
                conn,
                case_id,
                "integration_job_queued_manual",
                {
                    "provider": "onec",
                    "job_id": job_id,
                    "artifact_id": request.artifact_id,
                    "job_type": job_type
                }
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning(f"Ошибка при записи audit event в кейс {case_id}: {e}")
        
        return SyncOneCResponse(
            success=True,
            message="Задача отправлена в очередь 1С",
            job_id=job_id
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка при синхронизации в 1С: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Ошибка при синхронизации: {str(e)}")
