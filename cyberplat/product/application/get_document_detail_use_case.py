"""Use case: Get document detail for UI."""

import logging
from typing import Optional, Dict, Any

from cyberplat.product.domain.interfaces import ArtifactStateRepository

logger = logging.getLogger(__name__)


class GetDocumentDetailUseCase:
    """Use case для получения детальной информации о документе."""
    
    def __init__(
        self,
        artifact_state_repo: ArtifactStateRepository,
        artifact_service  # ArtifactService (legacy, для получения raw artifact)
    ):
        self.artifact_state_repo = artifact_state_repo
        self.artifact_service = artifact_service
    
    def execute(
        self,
        tenant_id: str,
        artifact_id: str
    ) -> Optional[Dict[str, Any]]:
        """
        Получить детальную информацию о документе.
        
        Args:
            tenant_id: ID тенанта
            artifact_id: ID артефакта документа
            
        Returns:
            DTO с детальной информацией или None
        """
        # Получаем артефакт
        artifact = self.artifact_service.get_artifact(artifact_id)
        if not artifact:
            return None
        
        # Проверяем tenant isolation
        if artifact.get("tenant_id") != tenant_id:
            return None
        
        # Проверяем kind
        if artifact.get("kind") != "document":
            return None
        
        # Получаем состояние
        state = self.artifact_state_repo.get_state(tenant_id, artifact_id)
        
        # Извлекаем ограниченные данные из artifact.data (не raw JSON)
        data = artifact.get("data", {})
        
        return {
            "id": artifact["id"],
            "kind": artifact["kind"],
            "source": artifact["source"],
            "tenant_id": artifact["tenant_id"],
            "created_at": artifact["created_at"],
            "state": {
                "ui_status": state["ui_status"] if state else "pending",
                "source_artifact_id": state["source_artifact_id"] if state else None,
                "error_code": state["error_code"] if state else None,
                "error_message": state["error_message"] if state else None,
                "confirmed_at": state["confirmed_at"] if state else None,
                "exported_at": state["exported_at"] if state else None,
                "export_target": state["export_target"] if state else None,
                "updated_at": state["updated_at"] if state else artifact["created_at"]
            },
            "filename": data.get("filename"),
            "file_id": data.get("file_id")
        }
