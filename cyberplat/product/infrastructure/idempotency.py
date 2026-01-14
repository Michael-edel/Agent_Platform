"""Utilities for computing idempotency keys for email OCR jobs."""

import hashlib
import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)


def compute_email_ocr_idempotency_key(
    tenant_id: str,
    email_from: str,
    email_to: str,
    email_subject: str,
    attachment_filename: str,
    attachment_size: int,
    attachment_content_sha256: str
) -> str:
    """
    Вычислить детерминированный idempotency_key для email OCR job.
    
    Формула: SHA256(tenant_id + from + to + subject + filename + size + content_sha256)
    
    Args:
        tenant_id: ID тенанта
        email_from: Email отправителя
        email_to: Email получателя
        email_subject: Тема письма
        attachment_filename: Имя файла вложения
        attachment_size: Размер вложения в байтах
        attachment_content_sha256: SHA256 хеш содержимого PDF (после base64 decode)
        
    Returns:
        SHA256 hex string (64 символа)
    """
    # Собираем строку для хеширования
    # ВАЖНО: порядок полей должен быть фиксированным для детерминированности
    key_string = (
        f"{tenant_id}|"
        f"{email_from}|"
        f"{email_to}|"
        f"{email_subject}|"
        f"{attachment_filename}|"
        f"{attachment_size}|"
        f"{attachment_content_sha256}"
    )
    
    # Вычисляем SHA256
    key_hash = hashlib.sha256(key_string.encode('utf-8')).hexdigest()
    
    logger.debug(f"Computed idempotency_key for email: {key_hash[:16]}... (tenant={tenant_id}, file={attachment_filename})")
    
    return key_hash


def compute_attachment_sha256(attachment_content: bytes) -> str:
    """
    Вычислить SHA256 хеш содержимого вложения.
    
    Args:
        attachment_content: Bytes содержимого PDF (после base64 decode)
        
    Returns:
        SHA256 hex string (64 символа)
    """
    return hashlib.sha256(attachment_content).hexdigest()
