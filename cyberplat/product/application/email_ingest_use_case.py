"""Use case: Email ingestion for documents (PDF → document artifact)."""

import logging
import uuid
import os
import hashlib
from typing import List, Dict, Any, Tuple, Optional
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)


class EmailIngestUseCase:
    """Use case для обработки входящих email с PDF вложениями."""
    
    def __init__(
        self,
        artifact_service,  # ArtifactService (legacy)
        event_service,  # EventService
        storage_service,  # StorageService
        artifact_state_repo,  # ArtifactStateRepository
        email_parser,  # EmailPayloadParser
        email_ocr_job_repo=None,  # EmailOcrJobRepository (optional, для auto-OCR)
        billing_enforcement=None,  # BillingEnforcementService (optional, для проверки квот)
        max_attachment_size_mb: int = 10
    ):
        self.artifact_service = artifact_service
        self.event_service = event_service
        self.storage_service = storage_service
        self.artifact_state_repo = artifact_state_repo
        self.email_parser = email_parser
        self.email_ocr_job_repo = email_ocr_job_repo
        self.billing_enforcement = billing_enforcement
        self.max_attachment_size_bytes = max_attachment_size_mb * 1024 * 1024
        
        # Feature flag для auto-OCR
        self.auto_ocr_enabled = os.getenv("EMAIL_AUTO_OCR_ENABLED", "0").strip() == "1"
    
    def execute(
        self,
        email_payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Обработать входящий email и создать document artifacts из PDF вложений.
        
        Args:
            email_payload: Raw payload от email provider (SendGrid/Mailgun/SES)
            
        Returns:
            {
                "success": bool,
                "tenant_id": str | None,
                "processed_attachments": int,
                "created_artifacts": List[str],
                "errors": List[str]
            }
        """
        errors = []
        created_artifacts = []
        
        try:
            # Парсим email payload (provider-agnostic)
            parsed_email = self.email_parser.parse(email_payload)
            
            # Определяем tenant_id из email адреса
            tenant_id = self._resolve_tenant_id(parsed_email["to"])
            
            if not tenant_id:
                error_msg = f"Could not resolve tenant_id from email address: {parsed_email['to']}"
                errors.append(error_msg)
                logger.warning(error_msg)
                
                # Эмитим событие об ошибке
                try:
                    self.event_service.emit(
                        event_type="email.ingest.failed",
                        tenant_id=None,  # Не можем определить tenant
                        artifact_id=None,
                        payload={
                            "from": parsed_email.get("from"),
                            "to": parsed_email.get("to"),
                            "subject": parsed_email.get("subject"),
                            "error": error_msg
                        }
                    )
                except Exception as e:
                    logger.error(f"Failed to emit email.ingest.failed event: {e}", exc_info=True)
                
                return {
                    "success": False,
                    "tenant_id": None,
                    "processed_attachments": 0,
                    "created_artifacts": [],
                    "errors": errors
                }
            
            # Обрабатываем вложения
            attachments = parsed_email.get("attachments", [])
            
            if not attachments:
                error_msg = "No attachments found in email"
                errors.append(error_msg)
                logger.warning(f"Email from {parsed_email['from']} has no attachments")
                
                # Эмитим событие о получении email без вложений
                try:
                    self.event_service.emit(
                        event_type="email.received",
                        tenant_id=tenant_id,
                        artifact_id=None,
                        payload={
                            "from": parsed_email.get("from"),
                            "to": parsed_email.get("to"),
                            "subject": parsed_email.get("subject"),
                            "attachments_count": 0,
                            "error": error_msg
                        }
                    )
                except Exception as e:
                    logger.error(f"Failed to emit email.received event: {e}", exc_info=True)
                
                return {
                    "success": False,
                    "tenant_id": tenant_id,
                    "processed_attachments": 0,
                    "created_artifacts": [],
                    "errors": errors
                }
            
            # Фильтруем только PDF вложения
            pdf_attachments = [
                att for att in attachments
                if att.get("content_type", "").lower() == "application/pdf"
            ]
            
            if not pdf_attachments:
                error_msg = "No PDF attachments found in email"
                errors.append(error_msg)
                logger.warning(f"Email from {parsed_email['from']} has no PDF attachments")
                
                # Эмитим событие
                try:
                    self.event_service.emit(
                        event_type="email.received",
                        tenant_id=tenant_id,
                        artifact_id=None,
                        payload={
                            "from": parsed_email.get("from"),
                            "to": parsed_email.get("to"),
                            "subject": parsed_email.get("subject"),
                            "attachments_count": len(attachments),
                            "pdf_attachments_count": 0,
                            "error": error_msg
                        }
                    )
                except Exception as e:
                    logger.error(f"Failed to emit email.received event: {e}", exc_info=True)
                
                return {
                    "success": False,
                    "tenant_id": tenant_id,
                    "processed_attachments": 0,
                    "created_artifacts": [],
                    "errors": errors
                }
            
            # Обрабатываем каждое PDF вложение
            for attachment in pdf_attachments:
                try:
                    artifact_id = self._process_pdf_attachment(
                        tenant_id=tenant_id,
                        attachment=attachment,
                        email_metadata={
                            "from": parsed_email.get("from"),
                            "to": parsed_email.get("to"),
                            "subject": parsed_email.get("subject"),
                            "attachment_name": attachment.get("filename", "unknown.pdf")
                        }
                    )
                    created_artifacts.append(artifact_id)
                    
                    # Если auto-OCR включен, создаём job для автоматического запуска OCR
                    if self.auto_ocr_enabled and self.email_ocr_job_repo:
                        try:
                            # Проверка квот перед созданием OCR job
                            if self.billing_enforcement:
                                try:
                                    self.billing_enforcement.enforce(
                                        tenant_id=tenant_id,
                                        required_metrics={
                                            "invoice_extracted": 1.0,
                                            "page_processed": 1.0
                                        },
                                        operation_name="email_auto_ocr"
                                    )
                                except Exception as quota_error:
                                    # Если квота превышена, не создаём job
                                    # Логируем и продолжаем (email уже обработан, job просто не создастся)
                                    logger.warning(
                                        f"Quota exceeded for email auto-OCR, skipping job creation: {quota_error}",
                                        exc_info=True
                                    )
                                    # Не создаём job, но email уже обработан
                                    continue
                            
                            self._create_ocr_job(
                                tenant_id=tenant_id,
                                document_artifact_id=artifact_id,
                                email_metadata={
                                    "from": parsed_email.get("from"),
                                    "to": parsed_email.get("to"),
                                    "subject": parsed_email.get("subject"),
                                    "attachment_name": attachment.get("filename", "unknown.pdf"),
                                    "attachment_size": attachment.get("size", 0),
                                    "attachment_content": attachment.get("content")
                                }
                            )
                        except Exception as job_error:
                            # Не падаем, если не удалось создать job (логируем)
                            logger.warning(
                                f"Failed to create OCR job for artifact {artifact_id}: {job_error}",
                                exc_info=True
                            )
                    
                except Exception as e:
                    error_msg = f"Failed to process attachment {attachment.get('filename', 'unknown')}: {str(e)}"
                    errors.append(error_msg)
                    logger.error(error_msg, exc_info=True)
            
            # Эмитим общее событие о получении email
            try:
                self.event_service.emit(
                    event_type="email.received",
                    tenant_id=tenant_id,
                    artifact_id=None,
                    payload={
                        "from": parsed_email.get("from"),
                        "to": parsed_email.get("to"),
                        "subject": parsed_email.get("subject"),
                        "attachments_count": len(attachments),
                        "pdf_attachments_count": len(pdf_attachments),
                        "processed_count": len(created_artifacts),
                        "errors_count": len(errors)
                    }
                )
            except Exception as e:
                logger.error(f"Failed to emit email.received event: {e}", exc_info=True)
            
            success = len(created_artifacts) > 0 and len(errors) == 0
            
            return {
                "success": success,
                "tenant_id": tenant_id,
                "processed_attachments": len(pdf_attachments),
                "created_artifacts": created_artifacts,
                "errors": errors
            }
            
        except Exception as e:
            error_msg = f"Email ingestion failed: {str(e)}"
            errors.append(error_msg)
            logger.error(error_msg, exc_info=True)
            
            return {
                "success": False,
                "tenant_id": None,
                "processed_attachments": 0,
                "created_artifacts": [],
                "errors": errors
            }
    
    def _resolve_tenant_id(self, email_to: str) -> Optional[str]:
        """
        Определить tenant_id из email адреса.
        
        Поддерживаемый формат: invoices+tenant-1@yourapp.ai
        
        Args:
            email_to: Email адрес получателя
            
        Returns:
            tenant_id или None если не удалось определить
        """
        if not email_to:
            return None
        
        # Парсим email адрес
        # Формат: invoices+tenant-1@yourapp.ai
        # Извлекаем часть после + и до @
        try:
            local_part = email_to.split("@")[0]
            if "+" in local_part:
                tenant_id = local_part.split("+")[1]
                # Валидация tenant_id (не пустой, не "string")
                tenant_id = tenant_id.strip()
                if tenant_id and tenant_id != "string":
                    return tenant_id
        except Exception:
            pass
        
        return None
    
    def _process_pdf_attachment(
        self,
        tenant_id: str,
        attachment: Dict[str, Any],
        email_metadata: Dict[str, Any]
    ) -> str:
        """
        Обработать PDF вложение: сохранить файл, создать artifact и state.
        
        Args:
            tenant_id: ID тенанта
            attachment: Данные вложения (filename, content, content_type, size)
            email_metadata: Метаданные email (from, to, subject)
            
        Returns:
            artifact_id созданного артефакта
            
        Raises:
            ValueError: Если вложение невалидно (размер, тип)
        """
        filename = attachment.get("filename", "unknown.pdf")
        content = attachment.get("content")  # base64 или bytes
        size = attachment.get("size", 0)
        
        # Валидация размера
        if size > self.max_attachment_size_bytes:
            raise ValueError(f"Attachment {filename} exceeds maximum size ({self.max_attachment_size_bytes / 1024 / 1024}MB)")
        
        # Валидация типа
        content_type = attachment.get("content_type", "").lower()
        if content_type != "application/pdf":
            raise ValueError(f"Attachment {filename} is not a PDF (content_type: {content_type})")
        
        # Декодируем base64 если нужно
        if isinstance(content, str):
            import base64
            try:
                file_bytes = base64.b64decode(content)
            except Exception as e:
                raise ValueError(f"Failed to decode base64 content: {e}")
        elif isinstance(content, bytes):
            file_bytes = content
        else:
            raise ValueError(f"Invalid attachment content type: {type(content)}")
        
        # Генерируем ID для артефакта и файла
        artifact_id = str(uuid.uuid4())
        file_id = f"{artifact_id}_{filename}"
        
        # Сохраняем файл (используем тот же механизм, что и upload)
        temp_dir = Path("out/jobs")
        temp_dir.mkdir(parents=True, exist_ok=True)
        temp_file = temp_dir / file_id
        
        try:
            temp_file.write_bytes(file_bytes)
        except Exception as e:
            raise ValueError(f"Failed to save file: {e}")
        
        # Создаём артефакт
        artifact_data = {
            "filename": filename,
            "file_id": file_id,
            "email_from": email_metadata.get("from"),
            "email_to": email_metadata.get("to"),
            "email_subject": email_metadata.get("subject"),
            "email_attachment_name": email_metadata.get("attachment_name")
        }
        
        artifact_id_created = self.artifact_service.create_artifact(
            kind="document",
            source="email",
            data=artifact_data,
            tenant_id=tenant_id
        )
        
        # Создаём artifact_state (ui_status="uploaded")
        try:
            self.artifact_state_repo.create_or_update_state(
                tenant_id=tenant_id,
                artifact_id=artifact_id_created,
                ui_status="uploaded"
            )
        except Exception as e:
            logger.warning(f"Failed to create artifact_state for {artifact_id_created}: {e}")
            # Не падаем, state может быть создан через event subscriber
        
        logger.info(
            f"Email attachment processed: artifact_id={artifact_id_created}, "
            f"filename={filename}, tenant_id={tenant_id}"
        )
        
        return artifact_id_created
    
    def _create_ocr_job(
        self,
        tenant_id: str,
        document_artifact_id: str,
        email_metadata: Dict[str, Any]
    ) -> str:
        """
        Создать email OCR job для автоматического запуска OCR.
        
        Args:
            tenant_id: ID тенанта
            document_artifact_id: ID созданного document artifact
            email_metadata: Метаданные email (from, to, subject, attachment_name, attachment_size, attachment_content)
            
        Returns:
            job_id
        """
        if not self.email_ocr_job_repo:
            raise ValueError("EmailOcrJobRepository not provided")
        
        # Вычисляем idempotency_key
        from cyberplat.product.infrastructure.idempotency import (
            compute_email_ocr_idempotency_key,
            compute_attachment_sha256
        )
        
        # Декодируем base64 если нужно для вычисления SHA256
        attachment_content = email_metadata.get("attachment_content")
        if isinstance(attachment_content, str):
            import base64
            try:
                content_bytes = base64.b64decode(attachment_content)
            except Exception as e:
                raise ValueError(f"Failed to decode base64 content for idempotency: {e}")
        elif isinstance(attachment_content, bytes):
            content_bytes = attachment_content
        else:
            raise ValueError(f"Invalid attachment content type: {type(attachment_content)}")
        
        attachment_sha256 = compute_attachment_sha256(content_bytes)
        
        idempotency_key = compute_email_ocr_idempotency_key(
            tenant_id=tenant_id,
            email_from=email_metadata.get("from", ""),
            email_to=email_metadata.get("to", ""),
            email_subject=email_metadata.get("subject", ""),
            attachment_filename=email_metadata.get("attachment_name", "unknown.pdf"),
            attachment_size=email_metadata.get("attachment_size", 0),
            attachment_content_sha256=attachment_sha256
        )
        
        # Получаем max_retries из env
        max_retries = int(os.getenv("EMAIL_AUTO_OCR_MAX_RETRIES", "5"))
        
        # Создаём job (или получаем существующий) с метаданными для inbox
        job_id = self.email_ocr_job_repo.create_job(
            tenant_id=tenant_id,
            document_artifact_id=document_artifact_id,
            idempotency_key=idempotency_key,
            max_retries=max_retries,
            email_from=email_metadata.get("from"),
            email_to=email_metadata.get("to"),
            email_subject=email_metadata.get("subject"),
            attachment_filename=email_metadata.get("attachment_name"),
            attachment_size=email_metadata.get("attachment_size")
        )
        
        # Эмитим событие о создании job
        try:
            self.event_service.emit(
                event_type="email.ocr.queued",
                tenant_id=tenant_id,
                artifact_id=document_artifact_id,
                payload={
                    "job_id": job_id,
                    "document_artifact_id": document_artifact_id
                }
            )
        except Exception as e:
            logger.warning(f"Failed to emit email.ocr.queued event: {e}")
        
        logger.info(
            f"Email OCR job created: job_id={job_id}, document={document_artifact_id}, "
            f"tenant={tenant_id}, idempotency_key={idempotency_key[:16]}..."
        )
        
        return job_id
