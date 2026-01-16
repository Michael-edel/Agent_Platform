"""Тесты для Billing Plans и Trial."""

import pytest
import os
from datetime import datetime, timedelta
from unittest.mock import Mock, patch

from cyberplat.billing_service import BillingService
from cyberplat.event_service import EventService
from cyberplat.artifact_service import ArtifactService
from cyberplat.product.infrastructure.database import get_sessionmaker
from cyberplat.product.infrastructure.plan_repositories_sqlalchemy import (
    PlanRepositoryImpl,
    TenantPlanRepositoryImpl
)
from cyberplat.product.application.assign_trial_use_case import AssignTrialUseCase
from cyberplat.product.application.billing_enforcement_service import (
    BillingEnforcementService,
    TrialExpiredError
)


@pytest.fixture
def db_session(monkeypatch, tmp_path):
    """Создать DB session для тестов."""
    import os
    # Используем изолированную БД для каждого теста
    db_path = tmp_path / "test_billing_plans.db"
    monkeypatch.setenv("PLATFORM_DB_PATH", str(db_path))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    
    from cyberplat.product.infrastructure.database import get_engine, get_sessionmaker
    from cyberplat.product.infrastructure.models import Base
    
    # Создаём таблицы
    engine = get_engine()
    Base.metadata.create_all(engine)
    
    SessionLocal = get_sessionmaker()
    session = SessionLocal()
    yield session
    session.close()


@pytest.fixture
def plan_repo(db_session):
    """Создать PlanRepository для тестов."""
    return PlanRepositoryImpl(session=db_session)


@pytest.fixture
def tenant_plan_repo(db_session):
    """Создать TenantPlanRepository для тестов."""
    return TenantPlanRepositoryImpl(session=db_session)


@pytest.fixture
def billing_service():
    """Создать BillingService для тестов."""
    return BillingService(db_path=":memory:")


@pytest.fixture
def artifact_service():
    """Создать ArtifactService для тестов."""
    return ArtifactService(db_path=":memory:")


@pytest.fixture
def event_service(artifact_service):
    """Создать EventService для тестов."""
    event_svc = EventService(db_path=":memory:", artifact_service=artifact_service)
    artifact_service.event_service = event_svc
    return event_svc


class TestTrialAssignment:
    """Тесты автоматического назначения trial."""
    
    def test_trial_assigned_on_first_tenant_use(
        self,
        plan_repo,
        tenant_plan_repo
    ):
        """Тест: trial автоматически назначается при первом использовании tenant."""
        tenant_id = "tenant-1"
        
        # Проверяем, что плана ещё нет
        assert tenant_plan_repo.get_tenant_plan(tenant_id) is None
        
        # Назначаем trial
        use_case = AssignTrialUseCase(
            tenant_plan_repo=tenant_plan_repo,
            plan_repo=plan_repo
        )
        success = use_case.execute(tenant_id=tenant_id)
        
        assert success is True
        
        # Проверяем, что план назначен
        tenant_plan = tenant_plan_repo.get_tenant_plan(tenant_id)
        assert tenant_plan is not None
        assert tenant_plan["plan_id"] == "trial"
        assert tenant_plan["expires_at"] is not None


class TestTrialExpiration:
    """Тесты истечения trial."""
    
    def test_trial_expires_and_blocks_operations(
        self,
        billing_service,
        event_service,
        plan_repo,
        tenant_plan_repo
    ):
        """Тест: истёкший trial блокирует операции."""
        tenant_id = "tenant-1"
        period = datetime.now().strftime("%Y-%m")
        
        # Назначаем trial с истёкшим сроком
        past_date = (datetime.now() - timedelta(days=1)).isoformat()
        tenant_plan_repo.assign_plan(
            tenant_id=tenant_id,
            plan_id="trial",
            expires_at=past_date
        )
        
        # Создаём enforcement service
        billing_enforcement = BillingEnforcementService(
            billing_service=billing_service,
            event_service=event_service,
            tenant_plan_repo=tenant_plan_repo,
            plan_repo=plan_repo
        )
        
        with patch.dict(os.environ, {"BILLING_ENFORCEMENT_ENABLED": "1"}):
            billing_enforcement.enabled = True
            
            # Должна быть заблокирована
            with pytest.raises(TrialExpiredError):
                billing_enforcement.enforce(
                    tenant_id=tenant_id,
                    required_metrics={"document_upload": 1.0},
                    operation_name="document_upload"
                )


class TestUpgrade:
    """Тесты upgrade плана."""
    
    def test_upgrade_changes_plan_and_unblocks(
        self,
        billing_service,
        event_service,
        plan_repo,
        tenant_plan_repo
    ):
        """Тест: upgrade меняет план и разблокирует операции."""
        tenant_id = "tenant-1"
        period = datetime.now().strftime("%Y-%m")
        
        # Назначаем trial с истёкшим сроком
        past_date = (datetime.now() - timedelta(days=1)).isoformat()
        tenant_plan_repo.assign_plan(
            tenant_id=tenant_id,
            plan_id="trial",
            expires_at=past_date
        )
        
        # Upgrade на pro
        success = tenant_plan_repo.assign_plan(
            tenant_id=tenant_id,
            plan_id="pro",
            expires_at=None  # Paid планы не истекают
        )
        
        assert success is True
        
        # Проверяем, что план обновлён
        tenant_plan = tenant_plan_repo.get_tenant_plan(tenant_id)
        assert tenant_plan["plan_id"] == "pro"
        assert tenant_plan["expires_at"] is None
        
        # Проверяем, что trial не истёк (т.к. это уже не trial)
        assert tenant_plan_repo.is_trial_expired(tenant_id) is False


class TestEnforcementWithPlans:
    """Тесты enforcement с планами."""
    
    def test_enforcement_uses_plan_quotas(
        self,
        billing_service,
        event_service,
        plan_repo,
        tenant_plan_repo
    ):
        """Тест: enforcement использует квоты из плана."""
        tenant_id = "tenant-1"
        period = datetime.now().strftime("%Y-%m")
        
        # Назначаем pro план
        tenant_plan_repo.assign_plan(
            tenant_id=tenant_id,
            plan_id="pro",
            expires_at=None
        )
        
        # Получаем план и проверяем квоты
        plan = plan_repo.get_plan("pro")
        assert plan is not None
        quotas = plan.get("quotas", {})
        assert "document_upload" in quotas
        assert quotas["document_upload"] == 100  # Pro plan quota
        
        # Создаём enforcement service
        billing_enforcement = BillingEnforcementService(
            billing_service=billing_service,
            event_service=event_service,
            tenant_plan_repo=tenant_plan_repo,
            plan_repo=plan_repo
        )
        
        # Проверяем, что квоты берутся из плана
        quota_status = billing_enforcement._get_quota_status(tenant_id=tenant_id, period=period)
        assert "metrics" in quota_status
        assert "document_upload" in quota_status["metrics"]
        assert quota_status["metrics"]["document_upload"]["monthly_quota"] == 100


class TestTenantIsolation:
    """Тесты tenant isolation для планов."""
    
    def test_tenant_isolation_plans(
        self,
        plan_repo,
        tenant_plan_repo
    ):
        """Тест: планы изолированы по tenant."""
        tenant_1 = "tenant-1"
        tenant_2 = "tenant-2"
        
        # Назначаем разные планы
        tenant_plan_repo.assign_plan(tenant_id=tenant_1, plan_id="trial", expires_at=None)
        tenant_plan_repo.assign_plan(tenant_id=tenant_2, plan_id="pro", expires_at=None)
        
        # Проверяем изоляцию
        plan_1 = tenant_plan_repo.get_tenant_plan(tenant_1)
        plan_2 = tenant_plan_repo.get_tenant_plan(tenant_2)
        
        assert plan_1["plan_id"] == "trial"
        assert plan_2["plan_id"] == "pro"


class TestWebhooks:
    """Тесты webhooks для планов."""
    
    def test_trial_expired_webhook_emitted(
        self,
        billing_service,
        event_service,
        plan_repo,
        tenant_plan_repo
    ):
        """Тест: при истёкшем trial эмитируется событие billing.trial.expired."""
        tenant_id = "tenant-1"
        
        # Назначаем trial с истёкшим сроком
        past_date = (datetime.now() - timedelta(days=1)).isoformat()
        tenant_plan_repo.assign_plan(
            tenant_id=tenant_id,
            plan_id="trial",
            expires_at=past_date
        )
        
        # Собираем эмитированные события
        emitted_events = []
        
        def capture_event(event_id, event_type, tenant_id, artifact_id, payload, created_at):
            if event_type == "billing.trial.expired":
                emitted_events.append({
                    "event_type": event_type,
                    "tenant_id": tenant_id,
                    "payload": payload
                })
        
        event_service.subscribe(capture_event)
        
        # Создаём enforcement service
        billing_enforcement = BillingEnforcementService(
            billing_service=billing_service,
            event_service=event_service,
            tenant_plan_repo=tenant_plan_repo,
            plan_repo=plan_repo
        )
        
        with patch.dict(os.environ, {"BILLING_ENFORCEMENT_ENABLED": "1"}):
            billing_enforcement.enabled = True
            
            try:
                billing_enforcement.enforce(
                    tenant_id=tenant_id,
                    required_metrics={"document_upload": 1.0},
                    operation_name="document_upload"
                )
            except TrialExpiredError:
                pass  # Ожидаем исключение
        
        # Проверяем, что событие было эмитировано
        assert len(emitted_events) == 1
        assert emitted_events[0]["event_type"] == "billing.trial.expired"
        assert emitted_events[0]["tenant_id"] == tenant_id
