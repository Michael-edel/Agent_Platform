"""Тесты для CaseService."""

import pytest
import tempfile
import os
from datetime import datetime

from cyberplat.case_service import CaseService, CaseNotFoundError, InvalidTransitionError


@pytest.fixture
def temp_db():
    """Создать временную БД для тестов (Windows-safe)."""
    import time
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield db_path
    if os.path.exists(db_path):
        # Retry логика для Windows (файл может быть временно заблокирован)
        max_retries = 5
        for attempt in range(max_retries):
            try:
                os.unlink(db_path)
                break
            except PermissionError:
                if attempt < max_retries - 1:
                    time.sleep(0.1)
                else:
                    import logging
                    logging.warning(f"Не удалось удалить временный файл {db_path} после {max_retries} попыток")


@pytest.fixture
def case_service(temp_db):
    """Создать CaseService для тестов."""
    return CaseService(db_path=temp_db)


@pytest.fixture
def case_service_memory():
    """Создать CaseService с in-memory БД для тестов."""
    service = CaseService(db_path=":memory:")
    yield service
    service.close()


def test_create_case_get_case(case_service_memory):
    """Тест: create_case -> get_case (happy path)."""
    tenant_id = "tenant-123"
    case_id = case_service_memory.create_case(
        tenant_id=tenant_id,
        case_type="support",
        title="Тестовый кейс"
    )
    
    assert case_id is not None
    assert len(case_id) > 0
    
    case = case_service_memory.get_case(case_id)
    assert case is not None
    assert case["id"] == case_id
    assert case["tenant_id"] == tenant_id
    assert case["case_type"] == "support"
    assert case["status"] == "open"
    assert case["title"] == "Тестовый кейс"
    assert case["current_step"] is None


def test_create_case_with_initial_step(case_service_memory):
    """Тест: create_case с initial_step."""
    tenant_id = "tenant-123"
    case_id = case_service_memory.create_case(
        tenant_id=tenant_id,
        case_type="support",
        title="Кейс с начальным шагом",
        initial_step="step1"
    )
    
    case = case_service_memory.get_case(case_id)
    assert case["current_step"] == "step1"


def test_list_cases_by_tenant_id(case_service_memory):
    """Тест: list_cases по tenant_id."""
    tenant1 = "tenant-1"
    tenant2 = "tenant-2"
    
    # Создаём кейсы для разных tenant'ов
    case1_id = case_service_memory.create_case(tenant1, "support", "Кейс 1")
    case2_id = case_service_memory.create_case(tenant1, "support", "Кейс 2")
    case3_id = case_service_memory.create_case(tenant2, "support", "Кейс 3")
    
    # Получаем кейсы для tenant1
    cases = case_service_memory.list_cases(tenant_id=tenant1)
    assert len(cases) == 2
    case_ids = [c["id"] for c in cases]
    assert case1_id in case_ids
    assert case2_id in case_ids
    assert case3_id not in case_ids
    
    # Получаем кейсы для tenant2
    cases = case_service_memory.list_cases(tenant_id=tenant2)
    assert len(cases) == 1
    assert cases[0]["id"] == case3_id


def test_list_cases_filter_by_status(case_service_memory):
    """Тест: list_cases с фильтром по status."""
    tenant_id = "tenant-123"
    
    case1_id = case_service_memory.create_case(tenant_id, "support", "Открытый кейс")
    case2_id = case_service_memory.create_case(tenant_id, "support", "Другой кейс")
    
    # Закрываем один кейс
    case_service_memory.close_case(case1_id)
    
    # Получаем только открытые
    open_cases = case_service_memory.list_cases(tenant_id=tenant_id, status="open")
    assert len(open_cases) == 1
    assert open_cases[0]["id"] == case2_id
    
    # Получаем только закрытые
    closed_cases = case_service_memory.list_cases(tenant_id=tenant_id, status="closed")
    assert len(closed_cases) == 1
    assert closed_cases[0]["id"] == case1_id


def test_add_task_complete_task(case_service_memory):
    """Тест: add_task -> complete_task (happy path)."""
    tenant_id = "tenant-123"
    case_id = case_service_memory.create_case(tenant_id, "support", "Кейс с задачей")
    
    task_id = case_service_memory.add_task(
        case_id=case_id,
        step_key="step1",
        title="Задача 1",
        assignee_role="support"
    )
    
    assert task_id is not None
    
    # Получаем задачу
    task = case_service_memory.get_task(task_id, case_id=case_id)
    assert task is not None
    assert task["status"] == "pending"
    
    # Завершаем задачу
    case_service_memory.complete_task(case_id, task_id)
    
    # Проверяем, что задача завершена
    task = case_service_memory.get_task(task_id, case_id=case_id)
    assert task["status"] == "completed"
    assert task["completed_at"] is not None


def test_complete_task_idempotent(case_service_memory):
    """Тест: повторное complete_task не падает (идемпотентность)."""
    tenant_id = "tenant-123"
    case_id = case_service_memory.create_case(tenant_id, "support", "Кейс")
    
    task_id = case_service_memory.add_task(case_id, "step1", "Задача")
    
    # Завершаем задачу первый раз
    case_service_memory.complete_task(case_id, task_id)
    
    # Завершаем задачу второй раз (не должно падать)
    case_service_memory.complete_task(case_id, task_id)
    
    # Проверяем, что задача всё ещё completed
    task = case_service_memory.get_task(task_id, case_id=case_id)
    assert task["status"] == "completed"


def test_transition_step_allowed(case_service_memory):
    """Тест: transition_step разрешённый переход проходит."""
    tenant_id = "tenant-123"
    case_id = case_service_memory.create_case(
        tenant_id=tenant_id,
        case_type="support",
        title="Кейс",
        initial_step="step1"
    )
    
    # Переводим на новый шаг
    case_service_memory.transition_step(case_id, "step2")
    
    case = case_service_memory.get_case(case_id)
    assert case["current_step"] == "step2"


def test_transition_step_forbidden_raises_error(case_service_memory):
    """Тест: transition_step запрещённый переход возвращает InvalidTransitionError."""
    tenant_id = "tenant-123"
    case_id = case_service_memory.create_case(
        tenant_id=tenant_id,
        case_type="support",
        title="Кейс",
        initial_step="step1"
    )
    
    # Закрываем кейс
    case_service_memory.close_case(case_id)
    
    # Пытаемся перевести закрытый кейс (должно упасть)
    with pytest.raises(InvalidTransitionError) as exc_info:
        case_service_memory.transition_step(case_id, "step2")
    
    # Проверяем, что сообщение на русском
    assert "закрытый" in str(exc_info.value).lower() or "closed" in str(exc_info.value).lower()


def test_transition_step_from_step_validation(case_service_memory):
    """Тест: transition_step проверяет from_step."""
    tenant_id = "tenant-123"
    case_id = case_service_memory.create_case(
        tenant_id=tenant_id,
        case_type="support",
        title="Кейс",
        initial_step="step1"
    )
    
    # Пытаемся перевести с неправильного шага (должно упасть)
    with pytest.raises(InvalidTransitionError) as exc_info:
        case_service_memory.transition_step(case_id, "step2", from_step="wrong_step")
    
    # Проверяем, что сообщение содержит информацию о несовпадении
    error_msg = str(exc_info.value)
    assert "не совпадает" in error_msg or "does not match" in error_msg.lower()


def test_close_case_changes_status(case_service_memory):
    """Тест: close_case меняет status на closed."""
    tenant_id = "tenant-123"
    case_id = case_service_memory.create_case(
        tenant_id=tenant_id,
        case_type="support",
        title="Кейс",
        initial_step="step1"
    )
    
    case = case_service_memory.get_case(case_id)
    assert case["status"] == "open"
    
    # Закрываем кейс
    case_service_memory.close_case(case_id)
    
    case = case_service_memory.get_case(case_id)
    assert case["status"] == "closed"
    assert case["current_step"] is None  # Шаг должен быть завершён


def test_close_case_idempotent(case_service_memory):
    """Тест: повторное close_case не падает."""
    tenant_id = "tenant-123"
    case_id = case_service_memory.create_case(tenant_id, "support", "Кейс")
    
    # Закрываем первый раз
    case_service_memory.close_case(case_id)
    
    # Закрываем второй раз (не должно падать)
    case_service_memory.close_case(case_id)
    
    case = case_service_memory.get_case(case_id)
    assert case["status"] == "closed"


def test_get_case_tenant_isolation(case_service_memory):
    """Тест: get_case с tenant_id изолирует данные."""
    tenant1 = "tenant-1"
    tenant2 = "tenant-2"
    
    case1_id = case_service_memory.create_case(tenant1, "support", "Кейс 1")
    
    # Получаем кейс с правильным tenant_id
    case = case_service_memory.get_case(case1_id, tenant_id=tenant1)
    assert case is not None
    
    # Получаем кейс с неправильным tenant_id (должен вернуть None)
    case = case_service_memory.get_case(case1_id, tenant_id=tenant2)
    assert case is None


def test_add_task_case_not_found(case_service_memory):
    """Тест: add_task для несуществующего кейса возвращает CaseNotFoundError."""
    with pytest.raises(CaseNotFoundError):
        case_service_memory.add_task(
            case_id="non-existent",
            step_key="step1",
            title="Задача"
        )


def test_complete_task_case_not_found(case_service_memory):
    """Тест: complete_task для несуществующего кейса возвращает CaseNotFoundError."""
    with pytest.raises(CaseNotFoundError):
        case_service_memory.complete_task("non-existent", "task-id")


def test_audit_events_created(case_service_memory):
    """Тест: audit events создаются для всех действий."""
    tenant_id = "tenant-123"
    case_id = case_service_memory.create_case(tenant_id, "support", "Кейс")
    
    # Проверяем, что событие создания записано
    conn = case_service_memory._get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM case_events WHERE case_id = ? AND event_type = ?", (case_id, "case.created"))
    event = cur.fetchone()
    conn.close()
    
    assert event is not None
    
    # Добавляем задачу и проверяем событие
    task_id = case_service_memory.add_task(case_id, "step1", "Задача")
    
    conn = case_service_memory._get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM case_events WHERE case_id = ? AND event_type = ?", (case_id, "task.created"))
    event = cur.fetchone()
    conn.close()
    
    assert event is not None
