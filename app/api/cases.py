"""API роутер для работы с кейсами."""

import logging
from typing import Optional, List
from fastapi import APIRouter, HTTPException, Header, Depends
from pydantic import BaseModel

from cyberplat.case_service import CaseService, CaseNotFoundError, InvalidTransitionError

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


# Dependency для получения CaseService
def get_case_service() -> CaseService:
    """Получить экземпляр CaseService."""
    return CaseService()


@router.post("/cases", response_model=CaseResponse, status_code=201)
async def create_case(
    request: CreateCaseRequest,
    x_tenant_id: Optional[str] = Header(None, alias="X-Tenant-ID"),
    case_service: CaseService = Depends(get_case_service)
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
    case_service: CaseService = Depends(get_case_service)
):
    """
    Добавить задачу к кейсу.
    
    Требует заголовок X-Tenant-ID.
    """
    if not x_tenant_id:
        raise HTTPException(status_code=400, detail="X-Tenant-ID header обязателен")
    
    try:
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
    case_service: CaseService = Depends(get_case_service)
):
    """
    Завершить задачу.
    
    Требует заголовок X-Tenant-ID.
    """
    if not x_tenant_id:
        raise HTTPException(status_code=400, detail="X-Tenant-ID header обязателен")
    
    try:
        case_service.complete_task(case_id, task_id)
        return {"status": "ok", "message": "Задача завершена"}
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
    case_service: CaseService = Depends(get_case_service)
):
    """
    Перевести кейс на новый шаг.
    
    Требует заголовок X-Tenant-ID.
    """
    if not x_tenant_id:
        raise HTTPException(status_code=400, detail="X-Tenant-ID header обязателен")
    
    try:
        case_service.transition_step(
            case_id=case_id,
            new_step=request.new_step,
            from_step=request.from_step
        )
        return {"status": "ok", "message": "Кейс переведён на новый шаг"}
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
    case_service: CaseService = Depends(get_case_service)
):
    """
    Закрыть кейс.
    
    Требует заголовок X-Tenant-ID.
    """
    if not x_tenant_id:
        raise HTTPException(status_code=400, detail="X-Tenant-ID header обязателен")
    
    try:
        case_service.close_case(case_id)
        return {"status": "ok", "message": "Кейс закрыт"}
    except CaseNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Ошибка при закрытии кейса: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Ошибка при закрытии кейса: {str(e)}")
