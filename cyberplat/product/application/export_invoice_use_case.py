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
    ) -> Tuple[bool, Optional[str], Optional[str], Optional[str]]:
        """
        Экспортировать инвойс.
        
        Args:
            tenant_id: ID тенанта
            artifact_id: ID артефакта инвойса
            export_type: Тип экспорта ('excel', 'json', '1c', etc.)
            export_config: Конфигурация экспорта (опционально)
            
        Returns:
            (success, export_id, file_id, error_message)
        """
        # Проверяем, что артефакт существует и принадлежит tenant
        state = self.artifact_state_repo.get_state(
            tenant_id=tenant_id,
            artifact_id=artifact_id
        )
        
        if not state:
            return False, None, None, f"Artifact {artifact_id} not found or does not belong to tenant {tenant_id}"
        
        # Создаём запись об экспорте (status="pending" по умолчанию)
        export_id = self.export_repo.create_export(
            tenant_id=tenant_id,
            artifact_id=artifact_id,
            export_type=export_type,
            export_config=export_config
        )
        
        logger.info(f"Export created: export_id={export_id}, artifact_id={artifact_id}, type={export_type}, tenant_id={tenant_id}")
        
        try:
            # Генерируем файл экспорта (синхронно, MVP)
            # TODO: В будущем можно сделать асинхронную генерацию через очередь
            file_id, file_path = self._generate_export_file(
                tenant_id=tenant_id,
                artifact_id=artifact_id,
                export_type=export_type,
                export_config=export_config,
                state=state
            )
            
            # Обновляем экспорт: статус="completed", file_id, file_path, completed_at
            self.export_repo.update_export(
                export_id=export_id,
                status="completed",
                file_id=file_id,
                file_path=file_path
            )
            
            # Обновляем artifact_state: ui_status="exported", export_target, exported_at
            self.artifact_state_repo.mark_exported(
                tenant_id=tenant_id,
                artifact_id=artifact_id,
                export_target=export_type
            )
            
            logger.info(f"Export completed: export_id={export_id}, file_id={file_id}, tenant_id={tenant_id}")
            return True, export_id, file_id, None
            
        except Exception as e:
            # Обновляем экспорт: статус="failed", error_message
            error_message = str(e)
            self.export_repo.update_export(
                export_id=export_id,
                status="failed",
                error_message=error_message
            )
            
            logger.error(f"Export failed: export_id={export_id}, error={e}", exc_info=True)
            return False, export_id, None, error_message
    
    def _generate_export_file(
        self,
        tenant_id: str,
        artifact_id: str,
        export_type: str,
        export_config: Optional[Dict[str, Any]],
        state: Dict[str, Any]
    ) -> Tuple[str, str]:
        """
        Генерировать файл экспорта.
        
        Args:
            tenant_id: ID тенанта
            artifact_id: ID артефакта инвойса
            export_type: Тип экспорта ('excel', 'json', '1c', etc.)
            export_config: Конфигурация экспорта
            state: ArtifactState данные
            
        Returns:
            (file_id, file_path)
        """
        # MVP: Простая генерация файла
        # TODO: В будущем добавить реальную генерацию Excel/JSON/1C
        
        from pathlib import Path
        import os
        
        # Создаём директорию для экспортов
        export_dir = Path("out/exports")
        export_dir.mkdir(parents=True, exist_ok=True)
        
        # Генерируем file_id и file_path
        file_id = f"{artifact_id}_{export_type}_{uuid.uuid4().hex[:8]}"
        
        if export_type == "json":
            file_path = export_dir / f"{file_id}.json"
            # MVP: Просто сохраняем пустой JSON (в будущем - реальные данные из artifact)
            import json as json_lib
            with open(file_path, "w", encoding="utf-8") as f:
                json_lib.dump({
                    "artifact_id": artifact_id,
                    "tenant_id": tenant_id,
                    "export_type": export_type,
                    "exported_at": datetime.now().isoformat(),
                    "note": "MVP export - real data generation TODO"
                }, f, indent=2, ensure_ascii=False)
        elif export_type == "excel":
            file_path = export_dir / f"{file_id}.xlsx"
            # MVP: Создаём пустой Excel файл (в будущем - реальная генерация)
            # Для MVP просто создаём текстовый файл с метаданными
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(f"Excel export for artifact {artifact_id}\n")
                f.write(f"Tenant: {tenant_id}\n")
                f.write(f"Export type: {export_type}\n")
                f.write(f"Exported at: {datetime.now().isoformat()}\n")
                f.write("Note: MVP export - real Excel generation TODO\n")
        else:
            # Другие типы экспорта (1c, etc.)
            file_path = export_dir / f"{file_id}.txt"
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(f"Export for artifact {artifact_id}\n")
                f.write(f"Tenant: {tenant_id}\n")
                f.write(f"Export type: {export_type}\n")
                f.write(f"Exported at: {datetime.now().isoformat()}\n")
                f.write("Note: MVP export - real generation TODO\n")
        
        return file_id, str(file_path)
