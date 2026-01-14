"""Use case: Get invoices list for UI."""

import logging
from typing import List, Dict, Any

from cyberplat.product.domain.interfaces import ArtifactStateRepository

logger = logging.getLogger(__name__)


class GetInvoicesUseCase:
    """Use case для получения списка инвойсов для UI."""
    
    def __init__(
        self,
        artifact_state_repo: ArtifactStateRepository
    ):
        self.artifact_state_repo = artifact_state_repo
    
    def execute(
        self,
        tenant_id: str,
        ui_status: str = None,
        limit: int = 100,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        """
        Получить список инвойсов для UI.
        
        Args:
            tenant_id: ID тенанта
            ui_status: Фильтр по UI статусу (опционально)
            limit: Лимит результатов
            offset: Смещение для пагинации
            
        Returns:
            Список инвойсов с UI-проекциями (без raw OCR JSON)
        """
        return self.artifact_state_repo.list_by_kind_and_status(
            tenant_id=tenant_id,
            kind="invoice",
            ui_status=ui_status,
            limit=limit,
            offset=offset
        )
