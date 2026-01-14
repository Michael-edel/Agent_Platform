"""Use case: Export invoice to Excel/JSON/etc."""

import logging
import uuid
from typing import Tuple, Optional, Dict, Any
from datetime import datetime

from cyberplat.product.domain.interfaces import ArtifactStateRepository, ExportRepository

logger = logging.getLogger(__name__)


class ExportInvoiceUseCase:
    """Use case для экспорта инвойса."""
    
    def __init__(
        self,
        artifact_state_repo: ArtifactStateRepository,
        export_repo: ExportRepository
    ):
        self.artifact_state_repo = artifact_state_repo
        self.export_repo = export_repo
    
    def execute(
        self,
        tenant_id: str,
        artifact_id: str,
        export_type: str,
        export_config: Optional[Dict[str, Any]] = None
    ) -> Tuple[bool, Optional[str], Optional[str]]:
        """
        Экспортировать инвойс.
        
        Args:
            tenant_id: ID тенанта
            artifact_id: ID артефакта инвойса
            export_type: Тип экспорта ('excel', 'json', '1c', etc.)
            export_config: Конфигурация экспорта (опционально)
            
        Returns:
            (success, export_id, error_message)
        """
        # Проверяем, что артефакт существует и принадлежит tenant
        state = self.artifact_state_repo.get_state(
            tenant_id=tenant_id,
            artifact_id=artifact_id
        )
        
        if not state:
            return False, None, f"Artifact {artifact_id} not found or does not belong to tenant {tenant_id}"
        
        # Создаём запись об экспорте
        export_id = self.export_repo.create_export(
            tenant_id=tenant_id,
            artifact_id=artifact_id,
            export_type=export_type,
            export_config=export_config
        )
        
        logger.info(f"Export created: export_id={export_id}, artifact_id={artifact_id}, type={export_type}, tenant_id={tenant_id}")
        
        # TODO: Здесь будет логика генерации файла экспорта
        # Пока возвращаем export_id для асинхронной обработки
        
        return True, export_id, None
