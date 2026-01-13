"""Подписчик на события для автоматического экспорта в S3."""

import json
import logging
from typing import Optional, Dict, Any
from pathlib import Path

from cyberplat.s3_exporter import S3Exporter
from cyberplat.artifact_service import ArtifactService
from cyberplat.storage_service import StorageService

logger = logging.getLogger(__name__)


class S3ExportSubscriber:
    """
    Подписчик на события для автоматического экспорта артефактов и событий в S3.
    
    ВАЖНО: Экспорт создает юридически значимый журнал действий SaaS.
    - Все экспорты идемпотентны (не перезаписывают существующие объекты)
    - Структура ключей: {prefix}/{tenant_id}/{category}/{artifact_id}/{filename}
    - События экспортируются как ledger для аудита
    - JSON детерминированный для сравнения байтов
    """
    
    def __init__(
        self,
        s3_exporter: S3Exporter,
        artifact_service: ArtifactService,
        storage_service: StorageService
    ):
        """
        Инициализировать подписчик.
        
        Args:
            s3_exporter: Экспортер S3
            artifact_service: Сервис артефактов
            storage_service: Сервис хранилища файлов
        """
        self.s3_exporter = s3_exporter
        self.artifact_service = artifact_service
        self.storage_service = storage_service
    
    def __call__(
        self,
        event_id: str,
        event_type: str,
        tenant_id: str,
        artifact_id: Optional[str],
        payload: Optional[Dict[str, Any]],
        created_at: str
    ):
        """
        Обработать событие и экспортировать данные в S3.
        
        Args:
            event_id: ID события
            event_type: Тип события
            tenant_id: ID тенанта
            artifact_id: ID артефакта
            payload: Дополнительные данные события
            created_at: Время создания события
        """
        # Проверяем, включен ли экспорт
        if not self.s3_exporter.enabled():
            return
        
        # Валидация tenant_id
        if not tenant_id or tenant_id.strip() == "" or tenant_id == "string":
            logger.warning(f"Пропущено событие {event_type} (event_id={event_id}): некорректный tenant_id")
            return
        
        tenant_id = tenant_id.strip()
        
        try:
            # Обрабатываем разные типы событий
            if event_type == "artifact.created":
                self._handle_artifact_created(event_id, tenant_id, artifact_id, payload)
            elif event_type == "document.extracted":
                self._handle_document_extracted(event_id, tenant_id, artifact_id, payload)
            elif event_type == "payment.ready":
                self._handle_payment_ready(event_id, tenant_id, artifact_id, payload)
            elif event_type == "payment.invalid":
                self._handle_payment_invalid(event_id, tenant_id, artifact_id, payload)
            
            # Экспортируем само событие (опционально, но полезно)
            self._export_event(event_id, event_type, tenant_id, artifact_id, payload, created_at)
            
        except Exception as e:
            logger.error(
                f"Ошибка при экспорте события {event_type} (event_id={event_id}): {e}",
                exc_info=True
            )
            # Не падаем, продолжаем работу
    
    def _handle_artifact_created(
        self,
        event_id: str,
        tenant_id: str,
        artifact_id: Optional[str],
        payload: Optional[Dict[str, Any]]
    ):
        """Обработать событие artifact.created."""
        if not artifact_id:
            return
        
        artifact = self.artifact_service.get_artifact(artifact_id)
        if not artifact:
            logger.warning(f"Артефакт {artifact_id} не найден для экспорта")
            return
        
        kind = artifact.get("kind")
        
        # Экспортируем PDF для kind=document
        if kind == "document":
            self._export_document_pdf(tenant_id, artifact_id, artifact)
    
    def _handle_document_extracted(
        self,
        event_id: str,
        tenant_id: str,
        artifact_id: Optional[str],
        payload: Optional[Dict[str, Any]]
    ):
        """Обработать событие document.extracted (invoice artifact)."""
        if not artifact_id:
            return
        
        artifact = self.artifact_service.get_artifact(artifact_id)
        if not artifact:
            logger.warning(f"Артефакт {artifact_id} не найден для экспорта")
            return
        
        kind = artifact.get("kind")
        
        # Экспортируем invoice.json
        if kind == "invoice":
            self._export_invoice_json(tenant_id, artifact_id, artifact)
    
    def _handle_payment_ready(
        self,
        event_id: str,
        tenant_id: str,
        artifact_id: Optional[str],
        payload: Optional[Dict[str, Any]]
    ):
        """Обработать событие payment.ready."""
        if not artifact_id:
            return
        
        artifact = self.artifact_service.get_artifact(artifact_id)
        if not artifact:
            logger.warning(f"Артефакт {artifact_id} не найден для экспорта")
            return
        
        kind = artifact.get("kind")
        
        # Экспортируем payment.json
        if kind == "payment":
            self._export_payment_json(tenant_id, artifact_id, artifact)
    
    def _handle_payment_invalid(
        self,
        event_id: str,
        tenant_id: str,
        artifact_id: Optional[str],
        payload: Optional[Dict[str, Any]]
    ):
        """Обработать событие payment.invalid."""
        if not artifact_id:
            return
        
        artifact = self.artifact_service.get_artifact(artifact_id)
        if not artifact:
            logger.warning(f"Артефакт {artifact_id} не найден для экспорта")
            return
        
        kind = artifact.get("kind")
        
        # Экспортируем payment.json (даже если invalid)
        if kind == "payment":
            self._export_payment_json(tenant_id, artifact_id, artifact)
    
    def _export_document_pdf(self, tenant_id: str, artifact_id: str, artifact: Dict[str, Any]):
        """Экспортировать PDF документа."""
        data = artifact.get("data", {})
        file_id = data.get("file_id")
        
        if not file_id:
            logger.warning(f"file_id не найден в артефакте {artifact_id}")
            return
        
        # Получаем путь к файлу
        file_path = self.storage_service.get_file_path(file_id)
        if not file_path or not file_path.exists():
            logger.warning(f"Файл не найден: {file_id} (artifact_id={artifact_id})")
            return
        
        # Строим S3 ключ: {prefix}/{tenant_id}/documents/{artifact_id}/{file_id}.pdf
        key = self.s3_exporter._build_key(
            tenant_id,
            "documents",
            artifact_id,
            f"{file_id}.pdf"
        )
        
        # Экспортируем PDF (WORM: не перезаписываем существующие файлы)
        # Используем put_file, который внутри использует put_bytes с allow_overwrite=False
        success = self.s3_exporter.put_file(key, str(file_path), content_type="application/pdf")
        if success:
            logger.info(f"Экспортирован PDF: {key}")
    
    def _export_invoice_json(self, tenant_id: str, artifact_id: str, artifact: Dict[str, Any]):
        """Экспортировать invoice.json."""
        # Строим S3 ключ: {prefix}/{tenant_id}/invoices/{artifact_id}.json
        key = self.s3_exporter._build_key(
            tenant_id,
            "invoices",
            f"{artifact_id}.json"
        )
        
        # Экспортируем только artifact.data (без метаданных для детерминированности)
        # WORM: не перезаписываем существующие объекты
        success = self.s3_exporter.put_json(key, artifact.get("data", {}), allow_overwrite=False)
        if success:
            logger.info(f"Экспортирован invoice: {key}")
    
    def _export_payment_json(self, tenant_id: str, artifact_id: str, artifact: Dict[str, Any]):
        """Экспортировать payment.json."""
        # Строим S3 ключ: {prefix}/{tenant_id}/payments/{artifact_id}.json
        key = self.s3_exporter._build_key(
            tenant_id,
            "payments",
            f"{artifact_id}.json"
        )
        
        # Экспортируем только artifact.data (без метаданных для детерминированности)
        # WORM: не перезаписываем существующие объекты
        success = self.s3_exporter.put_json(key, artifact.get("data", {}), allow_overwrite=False)
        if success:
            logger.info(f"Экспортирован payment: {key}")
    
    def _export_event(
        self,
        event_id: str,
        event_type: str,
        tenant_id: str,
        artifact_id: Optional[str],
        payload: Optional[Dict[str, Any]],
        created_at: str
    ):
        """Экспортировать само событие в S3."""
        # Строим S3 ключ: {prefix}/{tenant_id}/events/{artifact_id}/{event_id}.json
        if artifact_id:
            key = self.s3_exporter._build_key(
                tenant_id,
                "events",
                artifact_id,
                f"{event_id}.json"
            )
        else:
            # Если нет artifact_id, используем только event_id
            key = self.s3_exporter._build_key(
                tenant_id,
                "events",
                f"{event_id}.json"
            )
        
        # Формируем объект события (детерминированный формат для аудита)
        event_obj = {
            "id": event_id,
            "event_type": event_type,
            "tenant_id": tenant_id,
            "artifact_id": artifact_id,
            "payload": payload,  # payload может быть None, но это нормально
            "created_at": created_at  # Единственный timestamp - детерминированный из события
        }
        
        # Экспортируем (WORM: не перезаписываем существующие события)
        success = self.s3_exporter.put_json(key, event_obj, allow_overwrite=False)
        if success:
            logger.debug(f"Экспортировано событие: {key}")
