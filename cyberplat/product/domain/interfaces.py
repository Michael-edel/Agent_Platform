"""Domain interfaces (ports) for product/UI layer."""

from abc import ABC, abstractmethod
from typing import Optional, Dict, Any, List


class ArtifactStateRepository(ABC):
    """Repository interface для artifact states."""
    
    @abstractmethod
    def get_state(
        self,
        tenant_id: str,
        artifact_id: str
    ) -> Optional[Dict[str, Any]]:
        """Получить состояние артефакта."""
        pass
    
    @abstractmethod
    def create_or_update_state(
        self,
        tenant_id: str,
        artifact_id: str,
        ui_status: str,
        source_artifact_id: Optional[str] = None,
        error_code: Optional[str] = None,
        error_message: Optional[str] = None
    ) -> str:
        """Создать или обновить состояние артефакта."""
        pass
    
    @abstractmethod
    def update_status(
        self,
        tenant_id: str,
        artifact_id: str,
        ui_status: str,
        error_code: Optional[str] = None,
        error_message: Optional[str] = None
    ) -> bool:
        """Обновить статус артефакта."""
        pass
    
    @abstractmethod
    def mark_confirmed(
        self,
        tenant_id: str,
        artifact_id: str
    ) -> bool:
        """Отметить артефакт как подтверждённый."""
        pass
    
    @abstractmethod
    def mark_exported(
        self,
        tenant_id: str,
        artifact_id: str,
        export_target: str
    ) -> bool:
        """Отметить артефакт как экспортированный."""
        pass
    
    @abstractmethod
    def list_by_kind_and_status(
        self,
        tenant_id: str,
        kind: str,
        ui_status: Optional[str] = None,
        limit: int = 100,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        """Получить список артефактов по kind и статусу."""
        pass


class ExportRepository(ABC):
    """Repository interface для exports."""
    
    @abstractmethod
    def create_export(
        self,
        tenant_id: str,
        artifact_id: str,
        export_type: str,
        export_config: Optional[Dict[str, Any]] = None
    ) -> str:
        """Создать запись об экспорте."""
        pass
    
    @abstractmethod
    def update_export(
        self,
        export_id: str,
        status: str,
        file_id: Optional[str] = None,
        file_path: Optional[str] = None,
        error_message: Optional[str] = None
    ) -> bool:
        """Обновить статус экспорта."""
        pass
    
    @abstractmethod
    def get_export(
        self,
        tenant_id: str,
        export_id: str
    ) -> Optional[Dict[str, Any]]:
        """Получить экспорт по ID."""
        pass
    
    @abstractmethod
    def list_exports(
        self,
        tenant_id: str,
        artifact_id: Optional[str] = None,
        export_type: Optional[str] = None,
        limit: int = 100,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        """Получить список экспортов."""
        pass


class EmailOcrJobRepository(ABC):
    """Repository interface для email OCR jobs."""
    
    @abstractmethod
    def create_job(
        self,
        tenant_id: str,
        document_artifact_id: str,
        idempotency_key: str,
        max_retries: int = 5,
        email_from: Optional[str] = None,
        email_to: Optional[str] = None,
        email_subject: Optional[str] = None,
        attachment_filename: Optional[str] = None,
        attachment_size: Optional[int] = None
    ) -> str:
        """
        Создать job (или вернуть существующий по idempotency_key).
        
        Returns:
            job_id (существующего или нового job)
        """
        pass
    
    @abstractmethod
    def get_job_by_idempotency_key(
        self,
        idempotency_key: str
    ) -> Optional[Dict[str, Any]]:
        """Получить job по idempotency_key."""
        pass
    
    @abstractmethod
    def claim_job_for_processing(
        self,
        job_id: str
    ) -> bool:
        """
        Атомарно "забрать" job для обработки (status: queued/failed → processing).
        
        Returns:
            True если job успешно забран, False если уже обрабатывается или done/dead
        """
        pass
    
    @abstractmethod
    def mark_job_done(
        self,
        job_id: str,
        invoice_artifact_id: Optional[str] = None
    ) -> bool:
        """Отметить job как выполненный."""
        pass
    
    @abstractmethod
    def mark_job_failed(
        self,
        job_id: str,
        error_message: str,
        next_run_at: str,
        attempts: int
    ) -> bool:
        """Отметить job как failed и запланировать retry."""
        pass
    
    @abstractmethod
    def mark_job_dead(
        self,
        job_id: str,
        error_message: str
    ) -> bool:
        """Отметить job как dead (превышен max_retries)."""
        pass
    
    @abstractmethod
    def get_jobs_for_processing(
        self,
        limit: int = 10
    ) -> List[Dict[str, Any]]:
        """
        Получить jobs готовые к обработке (status in (queued, failed) AND next_run_at <= now).
        
        Returns:
            List of jobs ordered by next_run_at ASC
        """
        pass
    
    @abstractmethod
    def get_job(
        self,
        job_id: str
    ) -> Optional[Dict[str, Any]]:
        """Получить job по ID."""
        pass
    
    @abstractmethod
    def list_inbox_emails(
        self,
        tenant_id: str,
        limit: int = 50,
        cursor: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Получить список email jobs для inbox (tenant-scoped).
        
        Returns:
            List of jobs ordered by created_at DESC
        """
        pass
    
    @abstractmethod
    def get_stuck_processing_jobs(
        self,
        timeout_seconds: int
    ) -> List[Dict[str, Any]]:
        """
        Получить jobs со status=processing, которые "залипли" (updated_at слишком старый).
        
        Args:
            timeout_seconds: Максимальное время обработки в секундах
            
        Returns:
            List of stuck jobs
        """
        pass
    
    @abstractmethod
    def count_processing_jobs(self) -> int:
        """
        Подсчитать количество jobs со status=processing (для concurrency limit).
        
        Returns:
            Количество processing jobs
        """
        pass


class WebhookRepository(ABC):
    """Repository interface для outgoing webhooks."""

    @abstractmethod
    def create_webhook(self, tenant_id: str, url: str, events: List[str], secret: str) -> str:
        pass

    @abstractmethod
    def get_webhook(self, tenant_id: str, webhook_id: str) -> Optional[Dict[str, Any]]:
        pass

    @abstractmethod
    def list_webhooks(self, tenant_id: str, active_only: bool = True) -> List[Dict[str, Any]]:
        pass

    @abstractmethod
    def list_active_webhooks(self, tenant_id: str, event_type: str) -> List[Dict[str, Any]]:
        pass

    @abstractmethod
    def update_webhook(
        self,
        tenant_id: str,
        webhook_id: str,
        url: Optional[str] = None,
        events: Optional[List[str]] = None,
        active: Optional[bool] = None,
    ) -> bool:
        pass

    @abstractmethod
    def rotate_secret(self, tenant_id: str, webhook_id: str, new_secret: str) -> bool:
        pass

    @abstractmethod
    def delete_webhook(self, tenant_id: str, webhook_id: str) -> bool:
        pass

    @abstractmethod
    def get_webhook_by_id(self, webhook_id: str) -> Optional[Dict[str, Any]]:
        """Internal lookup by ID (dispatcher)."""
        pass


class WebhookDeliveryRepository(ABC):
    """Repository interface для webhook deliveries."""

    @abstractmethod
    def create_delivery(
        self, webhook_id: str, event_type: str, payload: Dict[str, Any], max_retries: int = 5
    ) -> str:
        pass

    @abstractmethod
    def get_pending_deliveries(self, limit: int = 10) -> List[Dict[str, Any]]:
        pass

    @abstractmethod
    def mark_sent(self, delivery_id: str) -> bool:
        pass

    @abstractmethod
    def mark_failed(self, delivery_id: str, error_message: str, next_run_at: str, attempts: int) -> bool:
        pass

    @abstractmethod
    def mark_dead(self, delivery_id: str, error_message: str) -> bool:
        pass

    @abstractmethod
    def get_delivery(self, delivery_id: str) -> Optional[Dict[str, Any]]:
        pass


class PlanRepository(ABC):
    """Repository interface для plans (тарифные планы)."""
    
    @abstractmethod
    def get_plan(self, plan_id: str) -> Optional[Dict[str, Any]]:
        """Получить план по ID."""
        pass
    
    @abstractmethod
    def list_active_plans(self) -> List[Dict[str, Any]]:
        """Получить список активных планов."""
        pass


class TenantPlanRepository(ABC):
    """Repository interface для tenant_plans (назначенные планы)."""
    
    @abstractmethod
    def get_tenant_plan(self, tenant_id: str) -> Optional[Dict[str, Any]]:
        """Получить план tenant."""
        pass
    
    @abstractmethod
    def assign_plan(
        self,
        tenant_id: str,
        plan_id: str,
        expires_at: Optional[str] = None,
        subscription_status: Optional[str] = None,
        failed_charges: Optional[int] = None,
    ) -> bool:
        """
        Назначить план tenant (создать или обновить).
        
        Args:
            tenant_id: ID tenant
            plan_id: ID плана
            expires_at: ISO timestamp когда истекает (null для paid планов)
            
        Returns:
            True если успешно
        """
        pass
    
    @abstractmethod
    def is_trial_expired(self, tenant_id: str) -> bool:
        """
        Проверить, истёк ли trial для tenant.
        
        Returns:
            True если trial истёк (plan_id=trial и expires_at < now)
        """
        pass

    @abstractmethod
    def update_subscription_state(
        self,
        tenant_id: str,
        expires_at: Optional[str],
        subscription_status: str,
        failed_charges: int,
    ) -> bool:
        """Обновить subscription состояние без смены плана."""
        pass

    @abstractmethod
    def list_tenants_due_for_renewal(
        self,
        plan_ids: List[str],
        statuses: List[str],
        renew_before_iso: str,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Выбрать tenant_plans, которые нужно продлить в окне renew."""
        pass


class KaspiOrderRepository(ABC):
    """Repository interface для kaspi_orders."""

    @abstractmethod
    def create_order(self, tenant_id: str, plan_id: str, kaspi_order_id: str) -> str:
        """Создать запись kaspi заказа."""
        pass

    @abstractmethod
    def get_by_kaspi_order_id(self, kaspi_order_id: str) -> Optional[Dict[str, Any]]:
        """Получить заказ по kaspi_order_id."""
        pass

    @abstractmethod
    def mark_paid(self, kaspi_order_id: str, payload: Optional[Dict[str, Any]] = None) -> bool:
        """Отметить заказ как paid (идемпотентно)."""
        pass

    @abstractmethod
    def mark_failed(
        self,
        kaspi_order_id: str,
        error: str,
        payload: Optional[Dict[str, Any]] = None,
        status: str = "failed",
    ) -> bool:
        """Отметить заказ как failed/canceled (идемпотентно)."""
        pass
