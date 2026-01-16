"""Тесты для Payments API."""

import pytest
import tempfile
import os
from fastapi.testclient import TestClient

from app.main import app
from cyberplat.payments.payment_service import PaymentService
from cyberplat.case_service import CaseService


@pytest.fixture
def temp_db():
    """Создать временную БД для тестов (Windows-safe)."""
    import time
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield db_path
    if os.path.exists(db_path):
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
def services(temp_db):
    """Создать сервисы для тестов."""
    payment_service = PaymentService(db_path=temp_db)
    case_service = CaseService(db_path=temp_db)
    return payment_service, case_service


def test_create_payment_order_api(services):
    """Тест: POST /api/v1/payments/orders создаёт поручение."""
    payment_service, case_service = services
    
    from app.api.payments import get_payment_service, get_case_service
    
    app.dependency_overrides[get_payment_service] = lambda: payment_service
    app.dependency_overrides[get_case_service] = lambda: case_service
    
    try:
        client = TestClient(app)
        
        response = client.post(
            "/api/v1/payments/orders",
            headers={"X-Tenant-ID": "tenant-123"},
            json={
                "amount": 100000.0,
                "beneficiary_name": "ООО Получатель",
                "beneficiary_account_iban": "KZ123456789012345678",
                "purpose": "Оплата по договору",
                "created_by_role": "accountant"
            }
        )
        
        assert response.status_code == 201
        data = response.json()
        assert data["payment_id"] is not None
        assert data["status"] in {"created", "skipped"}
        assert data["reason"] is None
        assert data["case_id"] is None

        # Платёж должен быть создан в storage
        order = payment_service.get_payment_order(data["payment_id"], tenant_id="tenant-123")
        assert order is not None
        assert order["amount"] == 100000.0
        assert order["status"] == "draft"
        
    finally:
        app.dependency_overrides.clear()


def test_create_payment_order_api_export_failure_still_returns_payment_id(services, monkeypatch):
    """Тест: даже при сбое пост-интеграции API возвращает payment_id и payment остаётся создан."""
    payment_service, case_service = services

    from app.api.payments import get_payment_service, get_case_service

    app.dependency_overrides[get_payment_service] = lambda: payment_service
    app.dependency_overrides[get_case_service] = lambda: case_service

    def _boom(*args, **kwargs):
        raise RuntimeError("export endpoint unavailable")

    monkeypatch.setattr(payment_service, "run_post_create_integrations", _boom)

    try:
        client = TestClient(app)

        response = client.post(
            "/api/v1/payments/orders",
            headers={"X-Tenant-ID": "tenant-123"},
            json={
                "amount": 100000.0,
                "beneficiary_name": "ООО Получатель",
                "beneficiary_account_iban": "KZ123456789012345678",
                "purpose": "Оплата по договору",
                "created_by_role": "accountant",
            },
        )

        assert response.status_code == 201
        data = response.json()
        assert data["payment_id"] is not None
        assert data["status"] == "created"
        assert data["reason"] is not None

        # Платёж должен существовать, несмотря на сбой
        order = payment_service.get_payment_order(data["payment_id"], tenant_id="tenant-123")
        assert order is not None

        # И должен быть записан event payment.export_failed
        import sqlite3

        conn = sqlite3.connect(payment_service.db_path)
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT event_type, payload_json FROM payment_events WHERE payment_order_id = ?",
                (data["payment_id"],),
            )
            rows = cur.fetchall()
        finally:
            conn.close()

        assert any(r[0] == "payment.export_failed" for r in rows)

    finally:
        app.dependency_overrides.clear()


def test_get_payment_order_api(services):
    """Тест: GET /api/v1/payments/orders/{id} возвращает поручение."""
    payment_service, case_service = services
    
    order_id = payment_service.create_payment_order(
        tenant_id="tenant-123",
        amount=100000.0,
        beneficiary_name="ООО Тест",
        beneficiary_account_iban="KZ123456789012345678",
        purpose="Тест",
        created_by_role="accountant"
    )
    
    from app.api.payments import get_payment_service, get_case_service
    
    app.dependency_overrides[get_payment_service] = lambda: payment_service
    app.dependency_overrides[get_case_service] = lambda: case_service
    
    try:
        client = TestClient(app)
        
        response = client.get(
            f"/api/v1/payments/orders/{order_id}",
            headers={"X-Tenant-ID": "tenant-123"}
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == order_id
        assert data["amount"] == 100000.0
        
    finally:
        app.dependency_overrides.clear()


def test_list_payment_orders_api(services):
    """Тест: GET /api/v1/payments/orders возвращает список."""
    payment_service, case_service = services
    
    order_id = payment_service.create_payment_order(
        tenant_id="tenant-123",
        amount=100000.0,
        beneficiary_name="ООО Тест",
        beneficiary_account_iban="KZ123456789012345678",
        purpose="Тест",
        created_by_role="accountant"
    )
    
    from app.api.payments import get_payment_service, get_case_service
    
    app.dependency_overrides[get_payment_service] = lambda: payment_service
    app.dependency_overrides[get_case_service] = lambda: case_service
    
    try:
        client = TestClient(app)
        
        response = client.get(
            "/api/v1/payments/orders?tenant_id=tenant-123",
            headers={"X-Tenant-ID": "tenant-123"}
        )
        
        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) == 1
        assert data["items"][0]["id"] == order_id
        
    finally:
        app.dependency_overrides.clear()


def test_submit_for_approval_api(services):
    """Тест: POST /api/v1/payments/orders/{id}/submit отправляет на согласование."""
    payment_service, case_service = services
    
    order_id = payment_service.create_payment_order(
        tenant_id="tenant-123",
        amount=100000.0,
        beneficiary_name="ООО Тест",
        beneficiary_account_iban="KZ123456789012345678",
        purpose="Тест",
        created_by_role="accountant"
    )
    
    from app.api.payments import get_payment_service, get_case_service
    
    app.dependency_overrides[get_payment_service] = lambda: payment_service
    app.dependency_overrides[get_case_service] = lambda: case_service
    
    try:
        client = TestClient(app)
        
        response = client.post(
            f"/api/v1/payments/orders/{order_id}/submit",
            headers={"X-Tenant-ID": "tenant-123"}
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "согласование" in data["message"].lower() or "approval" in data["message"].lower()
        
    finally:
        app.dependency_overrides.clear()


def test_approve_api(services):
    """Тест: POST /api/v1/payments/orders/{id}/approve одобряет."""
    payment_service, case_service = services
    
    payment_service.upsert_payment_policy(
        tenant_id="tenant-123",
        enabled=True,
        thresholds=[{"max": 1000000, "roles": ["accountant"]}]
    )
    
    order_id = payment_service.create_payment_order(
        tenant_id="tenant-123",
        amount=100000.0,
        beneficiary_name="ООО Тест",
        beneficiary_account_iban="KZ123456789012345678",
        purpose="Тест",
        created_by_role="accountant"
    )
    
    payment_service.submit_for_approval(order_id, "tenant-123")
    
    from app.api.payments import get_payment_service, get_case_service
    
    app.dependency_overrides[get_payment_service] = lambda: payment_service
    app.dependency_overrides[get_case_service] = lambda: case_service
    
    try:
        client = TestClient(app)
        
        response = client.post(
            f"/api/v1/payments/orders/{order_id}/approve",
            headers={"X-Tenant-ID": "tenant-123"},
            json={"role": "accountant", "comment": "Одобрено"}
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        
    finally:
        app.dependency_overrides.clear()


def test_reject_api(services):
    """Тест: POST /api/v1/payments/orders/{id}/reject отклоняет."""
    payment_service, case_service = services
    
    payment_service.upsert_payment_policy(
        tenant_id="tenant-123",
        enabled=True,
        thresholds=[{"max": 1000000, "roles": ["accountant"]}]
    )
    
    order_id = payment_service.create_payment_order(
        tenant_id="tenant-123",
        amount=100000.0,
        beneficiary_name="ООО Тест",
        beneficiary_account_iban="KZ123456789012345678",
        purpose="Тест",
        created_by_role="accountant"
    )
    
    payment_service.submit_for_approval(order_id, "tenant-123")
    
    from app.api.payments import get_payment_service, get_case_service
    
    app.dependency_overrides[get_payment_service] = lambda: payment_service
    app.dependency_overrides[get_case_service] = lambda: case_service
    
    try:
        client = TestClient(app)
        
        response = client.post(
            f"/api/v1/payments/orders/{order_id}/reject",
            headers={"X-Tenant-ID": "tenant-123"},
            json={"role": "accountant", "comment": "Отклонено"}
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "отклонено" in data["message"].lower() or "rejected" in data["message"].lower()
        
    finally:
        app.dependency_overrides.clear()


def test_export_csv_api(services):
    """Тест: POST /api/v1/payments/orders/{id}/export экспортирует в CSV."""
    payment_service, case_service = services
    
    order_id = payment_service.create_payment_order(
        tenant_id="tenant-123",
        amount=100000.0,
        beneficiary_name="ООО Тест",
        beneficiary_account_iban="KZ123456789012345678",
        purpose="Тест",
        created_by_role="accountant"
    )
    
    payment_service.submit_for_approval(order_id, "tenant-123")
    # Auto-approve
    
    from app.api.payments import get_payment_service, get_case_service
    
    app.dependency_overrides[get_payment_service] = lambda: payment_service
    app.dependency_overrides[get_case_service] = lambda: case_service
    
    try:
        client = TestClient(app)
        
        response = client.post(
            f"/api/v1/payments/orders/{order_id}/export",
            headers={"X-Tenant-ID": "tenant-123"},
            json={"format": "csv"}
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["file_id"] is not None
        
    finally:
        app.dependency_overrides.clear()


def test_payment_policy_api(services):
    """Тест: GET/PUT /api/v1/payments/policy."""
    payment_service, case_service = services
    
    from app.api.payments import get_payment_service, get_case_service
    
    app.dependency_overrides[get_payment_service] = lambda: payment_service
    app.dependency_overrides[get_case_service] = lambda: case_service
    
    try:
        client = TestClient(app)
        
        # Создаём политику
        response = client.put(
            "/api/v1/payments/policy?tenant_id=tenant-123",
            headers={"X-Tenant-ID": "tenant-123"},
            json={
                "enabled": True,
                "thresholds": [{"max": 100000, "roles": ["accountant"]}]
            }
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["enabled"] is True
        
        # Получаем политику
        response = client.get(
            "/api/v1/payments/policy?tenant_id=tenant-123",
            headers={"X-Tenant-ID": "tenant-123"}
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["enabled"] is True
        assert len(data["thresholds"]) == 1
        
    finally:
        app.dependency_overrides.clear()
