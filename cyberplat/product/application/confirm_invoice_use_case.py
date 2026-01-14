"""Use case: Confirm invoice (mark as confirmed)."""

import logging
from typing import Tuple, Optional

from cyberplat.product.domain.interfaces import ArtifactStateRepository

logger = logging.getLogger(__name__)


class ConfirmInvoiceUseCase:
    """Use case для подтверждения инвойса."""
    
    def __init__(
        self,
        artifact_state_repo: ArtifactStateRepository
    ):
        self.artifact_state_repo = artifact_state_repo
    
    def execute(
        self,
        tenant_id: str,
        artifact_id: str
    ) -> Tuple[bool, Optional[str]]:
        """
        Подтвердить инвойс.
        
        Args:
            tenant_id: ID тенанта
            artifact_id: ID артефакта инвойса
            
        Returns:
            (success, error_message)
        """
        success = self.artifact_state_repo.mark_confirmed(
            tenant_id=tenant_id,
            artifact_id=artifact_id
        )
        
        if not success:
            return False, f"Artifact {artifact_id} not found or does not belong to tenant {tenant_id}"
        
        logger.info(f"Invoice confirmed: artifact_id={artifact_id}, tenant_id={tenant_id}")
        return True, None
