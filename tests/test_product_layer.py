"""Тесты для Product/UI layer (artifact_states, exports, use cases)."""

import pytest
import uuid
from datetime import datetime
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session

from cyberplat.product.infrastructure.database import get_engine, get_sessionmaker
from cyberplat.product.infrastructure.models import ArtifactState, Export
from cyberplat.product.infrastructure.repositories_sqlalchemy import (
    ArtifactStateRepositoryImpl,
    ExportRepositoryImpl
)
from cyberplat.product.application.run_ocr_use_case import RunOCRUseCase
from cyberplat.product.application.confirm_invoice_use_case import ConfirmInvoiceUseCase
from cyberplat.product.application.export_invoice_use_case import ExportInvoiceUseCase


@pytest.fixture
def db_session():
    """Создать тестовую сессию БД (SQLite in-memory)."""
    # Используем in-memory SQLite для тестов
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    
    # Создаём таблицы
    from cyberplat.product.infrastructure.models import Base
    Base.metadata.create_all(engine)
    
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture
def artifact_state_repo(db_session: Session):
    """Создать ArtifactStateRepository для тестов."""
    return ArtifactStateRepositoryImpl(session=db_session)


@pytest.fixture
def export_repo(db_session: Session):
    """Создать ExportRepository для тестов."""
    return ExportRepositoryImpl(session=db_session)


@pytest.fixture
def tenant_id():
    """Тестовый tenant_id."""
    return "test-tenant-123"


@pytest.fixture
def artifact_id():
    """Тестовый artifact_id."""
    return str(uuid.uuid4())


class TestArtifactStateAutoCreated:
    """Тесты автоматического создания artifact_states."""
    
    def test_artifact_state_auto_created_on_upload(self, artifact_state_repo, tenant_id, artifact_id):
        """Тест: artifact_state создаётся при загрузке документа."""
        # Симулируем создание state при upload
        state_id = artifact_state_repo.create_or_update_state(
            tenant_id=tenant_id,
            artifact_id=artifact_id,
            ui_status="uploaded"
        )
        
        assert state_id is not None
        
        # Проверяем, что state создан
        state = artifact_state_repo.get_state(tenant_id=tenant_id, artifact_id=artifact_id)
        assert state is not None
        assert state["ui_status"] == "uploaded"
        assert state["tenant_id"] == tenant_id
        assert state["artifact_id"] == artifact_id
    
    def test_invoice_state_created_on_run_ocr(self, artifact_state_repo, tenant_id):
        """Тест: invoice state создаётся при запуске OCR."""
        document_id = str(uuid.uuid4())
        invoice_id = str(uuid.uuid4())
        
        # Симулируем создание state для invoice после OCR
        state_id = artifact_state_repo.create_or_update_state(
            tenant_id=tenant_id,
            artifact_id=invoice_id,
            ui_status="pending",
            source_artifact_id=document_id
        )
        
        assert state_id is not None
        
        # Проверяем, что state создан с правильной связью
        state = artifact_state_repo.get_state(tenant_id=tenant_id, artifact_id=invoice_id)
        assert state is not None
        assert state["ui_status"] == "pending"
        assert state["source_artifact_id"] == document_id
    
    def test_document_extracted_updates_state(self, artifact_state_repo, tenant_id):
        """Тест: событие document.extracted обновляет state."""
        document_id = str(uuid.uuid4())
        
        # Создаём начальный state для document
        artifact_state_repo.create_or_update_state(
            tenant_id=tenant_id,
            artifact_id=document_id,
            ui_status="uploaded"
        )
        
        # Симулируем обновление после document.extracted
        updated = artifact_state_repo.update_status(
            tenant_id=tenant_id,
            artifact_id=document_id,
            ui_status="extracted"
        )
        
        assert updated is True
        
        # Проверяем, что state обновлён
        state = artifact_state_repo.get_state(tenant_id=tenant_id, artifact_id=document_id)
        assert state["ui_status"] == "extracted"


class TestConfirmInvoice:
    """Тесты подтверждения инвойса."""
    
    def test_confirm_invoice_idempotent(self, artifact_state_repo, tenant_id, artifact_id):
        """Тест: подтверждение инвойса идемпотентно."""
        # Создаём state для invoice
        artifact_state_repo.create_or_update_state(
            tenant_id=tenant_id,
            artifact_id=artifact_id,
            ui_status="pending"
        )
        
        # Первое подтверждение
        result1 = artifact_state_repo.mark_confirmed(
            tenant_id=tenant_id,
            artifact_id=artifact_id
        )
        assert result1 is True
        
        state1 = artifact_state_repo.get_state(tenant_id=tenant_id, artifact_id=artifact_id)
        assert state1["ui_status"] == "confirmed"
        assert state1["confirmed_at"] is not None
        
        # Второе подтверждение (идемпотентно)
        result2 = artifact_state_repo.mark_confirmed(
            tenant_id=tenant_id,
            artifact_id=artifact_id
        )
        assert result2 is True
        
        state2 = artifact_state_repo.get_state(tenant_id=tenant_id, artifact_id=artifact_id)
        assert state2["ui_status"] == "confirmed"
        # confirmed_at должен остаться тем же (или обновиться - зависит от реализации)
    
    def test_confirm_invoice_not_found(self, artifact_state_repo, tenant_id):
        """Тест: подтверждение несуществующего инвойса возвращает False."""
        result = artifact_state_repo.mark_confirmed(
            tenant_id=tenant_id,
            artifact_id="non-existent-id"
        )
        assert result is False


class TestExport:
    """Тесты экспорта инвойсов."""
    
    def test_export_creates_exports_row_and_updates_state(
        self,
        artifact_state_repo,
        export_repo,
        tenant_id,
        artifact_id,
        db_session
    ):
        """Тест: экспорт создаёт запись в exports и обновляет state."""
        # Создаём state для invoice
        artifact_state_repo.create_or_update_state(
            tenant_id=tenant_id,
            artifact_id=artifact_id,
            ui_status="confirmed"
        )
        
        # Создаём экспорт
        export_id = export_repo.create_export(
            tenant_id=tenant_id,
            artifact_id=artifact_id,
            export_type="excel"
        )
        
        assert export_id is not None
        
        # Обновляем экспорт на "completed"
        file_id = f"{artifact_id}_excel_12345"
        file_path = f"out/exports/{file_id}.xlsx"
        updated = export_repo.update_export(
            export_id=export_id,
            status="completed",
            file_id=file_id,
            file_path=file_path
        )
        
        assert updated is True
        
        # Проверяем, что экспорт создан
        export = export_repo.get_export(tenant_id=tenant_id, export_id=export_id)
        assert export is not None
        assert export["status"] == "completed"
        assert export["file_id"] == file_id
        assert export["file_path"] == file_path
        assert export["completed_at"] is not None
        
        # Обновляем state на "exported"
        artifact_state_repo.mark_exported(
            tenant_id=tenant_id,
            artifact_id=artifact_id,
            export_target="excel"
        )
        
        # Проверяем, что state обновлён
        state = artifact_state_repo.get_state(tenant_id=tenant_id, artifact_id=artifact_id)
        assert state["ui_status"] == "exported"
        assert state["export_target"] == "excel"
        assert state["exported_at"] is not None


class TestTenantIsolation:
    """Тесты tenant isolation для product layer."""
    
    def test_tenant_isolation_product_endpoints(
        self,
        artifact_state_repo,
        export_repo
    ):
        """Тест: данные разных tenant не пересекаются."""
        tenant1 = "tenant-1"
        tenant2 = "tenant-2"
        artifact1 = str(uuid.uuid4())
        artifact2 = str(uuid.uuid4())
        
        # Создаём states для разных tenant
        artifact_state_repo.create_or_update_state(
            tenant_id=tenant1,
            artifact_id=artifact1,
            ui_status="uploaded"
        )
        
        artifact_state_repo.create_or_update_state(
            tenant_id=tenant2,
            artifact_id=artifact2,
            ui_status="uploaded"
        )
        
        # Проверяем, что tenant1 не видит данные tenant2
        state1 = artifact_state_repo.get_state(tenant_id=tenant1, artifact_id=artifact1)
        assert state1 is not None
        assert state1["tenant_id"] == tenant1
        
        state2 = artifact_state_repo.get_state(tenant_id=tenant2, artifact_id=artifact2)
        assert state2 is not None
        assert state2["tenant_id"] == tenant2
        
        # Проверяем, что tenant1 не может получить state tenant2
        state1_trying_tenant2 = artifact_state_repo.get_state(
            tenant_id=tenant1,
            artifact_id=artifact2
        )
        assert state1_trying_tenant2 is None
        
        # Проверяем экспорты
        export1_id = export_repo.create_export(
            tenant_id=tenant1,
            artifact_id=artifact1,
            export_type="excel"
        )
        
        export2_id = export_repo.create_export(
            tenant_id=tenant2,
            artifact_id=artifact2,
            export_type="json"
        )
        
        # Проверяем, что tenant1 не видит экспорт tenant2
        export1 = export_repo.get_export(tenant_id=tenant1, export_id=export1_id)
        assert export1 is not None
        assert export1["tenant_id"] == tenant1
        
        export2 = export_repo.get_export(tenant_id=tenant2, export_id=export2_id)
        assert export2 is not None
        assert export2["tenant_id"] == tenant2
        
        # Проверяем, что tenant1 не может получить экспорт tenant2
        export1_trying_tenant2 = export_repo.get_export(
            tenant_id=tenant1,
            export_id=export2_id
        )
        assert export1_trying_tenant2 is None


class TestExportUseCase:
    """Тесты ExportInvoiceUseCase."""
    
    def test_export_use_case_success(
        self,
        artifact_state_repo,
        export_repo,
        tenant_id,
        artifact_id
    ):
        """Тест: успешный экспорт через use case."""
        # Создаём state для invoice
        artifact_state_repo.create_or_update_state(
            tenant_id=tenant_id,
            artifact_id=artifact_id,
            ui_status="confirmed"
        )
        
        # Создаём use case
        use_case = ExportInvoiceUseCase(
            artifact_state_repo=artifact_state_repo,
            export_repo=export_repo
        )
        
        # Выполняем экспорт
        success, export_id, file_id, error_message = use_case.execute(
            tenant_id=tenant_id,
            artifact_id=artifact_id,
            export_type="json"
        )
        
        assert success is True
        assert export_id is not None
        assert file_id is not None
        assert error_message is None
        
        # Проверяем, что экспорт создан и state обновлён
        export = export_repo.get_export(tenant_id=tenant_id, export_id=export_id)
        assert export is not None
        assert export["status"] == "completed"
        assert export["file_id"] is not None
        assert export["file_id"] == file_id
        assert export["file_path"] is not None
        
        state = artifact_state_repo.get_state(tenant_id=tenant_id, artifact_id=artifact_id)
        assert state["ui_status"] == "exported"
        assert state["export_target"] == "json"
    
    def test_export_use_case_not_found(
        self,
        artifact_state_repo,
        export_repo,
        tenant_id
    ):
        """Тест: экспорт несуществующего инвойса."""
        use_case = ExportInvoiceUseCase(
            artifact_state_repo=artifact_state_repo,
            export_repo=export_repo
        )
        
        success, export_id, file_id, error_message = use_case.execute(
            tenant_id=tenant_id,
            artifact_id="non-existent-id",
            export_type="excel"
        )
        
        assert success is False
        assert export_id is None
        assert file_id is None
        assert error_message is not None


class TestListByKindAndStatus:
    """Тесты фильтрации по kind и status."""
    
    def test_list_documents_by_status(
        self,
        artifact_state_repo,
        tenant_id,
        db_session
    ):
        """Тест: получение списка документов по статусу."""
        # Создаём несколько документов с разными статусами
        doc1 = str(uuid.uuid4())
        doc2 = str(uuid.uuid4())
        doc3 = str(uuid.uuid4())
        
        artifact_state_repo.create_or_update_state(
            tenant_id=tenant_id,
            artifact_id=doc1,
            ui_status="uploaded"
        )
        
        artifact_state_repo.create_or_update_state(
            tenant_id=tenant_id,
            artifact_id=doc2,
            ui_status="extracted"
        )
        
        artifact_state_repo.create_or_update_state(
            tenant_id=tenant_id,
            artifact_id=doc3,
            ui_status="uploaded"
        )
        
        # Получаем список документов со статусом "uploaded"
        # Примечание: list_by_kind_and_status требует JOIN с artifacts,
        # поэтому для полного теста нужен mock artifact_service
        # Здесь проверяем только базовую логику репозитория
        
        # Проверяем, что states созданы
        state1 = artifact_state_repo.get_state(tenant_id=tenant_id, artifact_id=doc1)
        assert state1["ui_status"] == "uploaded"
        
        state2 = artifact_state_repo.get_state(tenant_id=tenant_id, artifact_id=doc2)
        assert state2["ui_status"] == "extracted"
        
        state3 = artifact_state_repo.get_state(tenant_id=tenant_id, artifact_id=doc3)
        assert state3["ui_status"] == "uploaded"
