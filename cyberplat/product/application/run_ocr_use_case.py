"""Use case: Run OCR on document (trigger doc_agent)."""

import logging
from typing import Tuple, Optional

from cyberplat.product.domain.interfaces import ArtifactStateRepository

logger = logging.getLogger(__name__)


class RunOCRUseCase:
    """Use case для запуска OCR на документе."""
    
    def __init__(
        self,
        artifact_state_repo: ArtifactStateRepository,
        doc_agent,  # DocAgent instance
        artifact_service  # ArtifactService (legacy)
    ):
        self.artifact_state_repo = artifact_state_repo
        self.doc_agent = doc_agent
        self.artifact_service = artifact_service
    
    async def execute(
        self,
        tenant_id: str,
        artifact_id: str
    ) -> Tuple[bool, Optional[str], Optional[str]]:
        """
        Запустить OCR на документе.
        
        Args:
            tenant_id: ID тенанта
            artifact_id: ID артефакта документа
            
        Returns:
            (success, invoice_artifact_id, error_message)
        """
        # Получаем артефакт документа
        artifact = self.artifact_service.get_artifact(artifact_id)
        if not artifact:
            return False, None, f"Artifact {artifact_id} not found"
        
        # Проверяем tenant isolation
        if artifact.get("tenant_id") != tenant_id:
            return False, None, f"Artifact {artifact_id} does not belong to tenant {tenant_id}"
        
        # Проверяем kind
        if artifact.get("kind") != "document":
            return False, None, f"Artifact {artifact_id} is not a document (kind={artifact.get('kind')})"
        
        # Обновляем статус на 'processing'
        self.artifact_state_repo.create_or_update_state(
            tenant_id=tenant_id,
            artifact_id=artifact_id,
            ui_status="processing"
        )
        
        try:
            # Запускаем doc_agent
            from cyberplat.base_agent import AgentContext
            
            context = AgentContext(
                artifact_id=artifact_id,
                file_id=artifact.get("data", {}).get("file_id"),
                tenant_id=tenant_id
            )
            
            result = await self.doc_agent.run(context)
            
            if result.get("success"):
                invoice_artifact_id = result.get("artifact_id")
                
                # Создаём состояние для invoice с ссылкой на document
                if invoice_artifact_id:
                    self.artifact_state_repo.create_or_update_state(
                        tenant_id=tenant_id,
                        artifact_id=invoice_artifact_id,
                        ui_status="pending",
                        source_artifact_id=artifact_id  # Связь invoice → document
                    )
                
                # Обновляем статус document на 'completed'
                self.artifact_state_repo.update_status(
                    tenant_id=tenant_id,
                    artifact_id=artifact_id,
                    ui_status="completed"
                )
                
                logger.info(f"OCR completed: document={artifact_id}, invoice={invoice_artifact_id}, tenant_id={tenant_id}")
                return True, invoice_artifact_id, None
            else:
                # Обновляем статус на 'error'
                error_message = result.get("error", "Unknown error")
                self.artifact_state_repo.update_status(
                    tenant_id=tenant_id,
                    artifact_id=artifact_id,
                    ui_status="error",
                    error_message=error_message
                )
                return False, None, error_message
                
        except Exception as e:
            # Обновляем статус на 'error'
            error_message = str(e)
            self.artifact_state_repo.update_status(
                tenant_id=tenant_id,
                artifact_id=artifact_id,
                ui_status="error",
                error_message=error_message
            )
            logger.error(f"OCR failed: {e}", exc_info=True)
            return False, None, error_message
