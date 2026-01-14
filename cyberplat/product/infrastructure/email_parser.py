"""Email payload parser (provider-agnostic)."""

import logging
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)


class EmailPayloadParser:
    """Парсер email payload от различных email providers (SendGrid, Mailgun, SES)."""
    
    def parse(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Парсить email payload от email provider.
        
        Поддерживаемые форматы:
        - SendGrid Inbound Parse
        - Mailgun Inbound
        - AWS SES (через SNS)
        
        Args:
            payload: Raw JSON payload от email provider
            
        Returns:
            {
                "from": str,
                "to": str,
                "subject": str,
                "attachments": List[Dict[str, Any]]
            }
            
        Raises:
            ValueError: Если payload невалиден
        """
        # Пробуем определить provider по структуре payload.
        # Важно: и SendGrid, и Mailgun могут содержать поле "sender",
        # поэтому порядок проверок критичен.
        if "signature" in payload or ("sender" in payload and "recipient" in payload):
            # Mailgun Inbound
            return self._parse_mailgun(payload)
        if "envelope" in payload or ("from" in payload and "to" in payload):
            # SendGrid Inbound Parse
            return self._parse_sendgrid(payload)
        elif "Message" in payload or "mail" in payload:
            # AWS SES через SNS
            return self._parse_ses(payload)
        else:
            # Generic format (пробуем угадать)
            return self._parse_generic(payload)
    
    def _parse_sendgrid(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Парсить SendGrid Inbound Parse payload."""
        from_email = payload.get("from", "")
        to_email = payload.get("to", "")
        subject = payload.get("subject", "")
        
        # SendGrid отправляет attachments как массив объектов
        attachments = []
        attachments_raw = payload.get("attachments", [])
        
        if isinstance(attachments_raw, list):
            for att in attachments_raw:
                if isinstance(att, dict):
                    attachments.append({
                        "filename": att.get("filename", "unknown"),
                        # Поддерживаем и provider-agnostic формат (content_type), и sendgrid (type)
                        "content_type": (
                            att.get("content_type")
                            or att.get("contentType")
                            or att.get("type")
                            or "application/octet-stream"
                        ),
                        "content": att.get("content") or att.get("body") or att.get("data"),  # base64 string
                        "size": att.get("size", 0)
                    })
        
        return {
            "from": from_email,
            "to": to_email,
            "subject": subject,
            "attachments": attachments
        }
    
    def _parse_mailgun(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Парсить Mailgun Inbound payload."""
        # Mailgun inbound:
        # - sender -> from
        # - recipient -> to
        from_email = payload.get("sender", "") or payload.get("from", "")
        to_email = payload.get("recipient", "") or payload.get("to", "")
        subject = payload.get("subject", "")
        
        # Mailgun отправляет attachments как массив объектов
        attachments = []
        attachments_raw = payload.get("attachments", [])
        
        if isinstance(attachments_raw, list):
            for att in attachments_raw:
                if isinstance(att, dict):
                    size_raw = att.get("size", 0) or att.get("length", 0)
                    try:
                        size_val = int(size_raw)
                    except Exception:
                        size_val = 0
                    attachments.append({
                        "filename": att.get("filename", "unknown"),
                        "content_type": (
                            att.get("content-type")
                            or att.get("content_type")
                            or att.get("contentType")
                            or "application/octet-stream"
                        ),
                        # Mailgun может прислать base64 в body/content/data
                        "content": att.get("body") or att.get("content") or att.get("data"),
                        "size": size_val,
                    })
        
        return {
            "from": from_email,
            "to": to_email,
            "subject": subject,
            "attachments": attachments
        }
    
    def _parse_ses(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Парсить AWS SES через SNS payload."""
        # SES через SNS имеет вложенную структуру
        message = payload.get("Message", "{}")
        
        # Если Message это JSON string, парсим его
        if isinstance(message, str):
            import json
            try:
                message = json.loads(message)
            except:
                message = {}
        
        mail = message.get("mail", {})
        from_email = mail.get("source", "")
        to_email = mail.get("destination", [""])[0] if mail.get("destination") else ""
        subject = mail.get("commonHeaders", {}).get("subject", "")
        
        # SES attachments нужно получать отдельно через S3 или из notification
        # Для MVP предполагаем, что attachments приходят в payload
        attachments = []
        attachments_raw = payload.get("attachments", [])
        
        if isinstance(attachments_raw, list):
            for att in attachments_raw:
                if isinstance(att, dict):
                    attachments.append({
                        "filename": att.get("filename", "unknown"),
                        "content_type": att.get("contentType", "application/octet-stream"),
                        "content": att.get("content"),  # base64 string
                        "size": att.get("size", 0)
                    })
        
        return {
            "from": from_email,
            "to": to_email,
            "subject": subject,
            "attachments": attachments
        }
    
    def _parse_generic(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Парсить generic payload (пробуем угадать структуру)."""
        from_email = (
            payload.get("from") or
            payload.get("sender") or
            payload.get("email_from") or
            ""
        )
        
        to_email = (
            payload.get("to") or
            payload.get("recipient") or
            payload.get("email_to") or
            ""
        )
        
        subject = (
            payload.get("subject") or
            payload.get("email_subject") or
            ""
        )
        
        attachments = []
        attachments_raw = payload.get("attachments", payload.get("attachment", []))
        
        if isinstance(attachments_raw, list):
            for att in attachments_raw:
                if isinstance(att, dict):
                    attachments.append({
                        "filename": att.get("filename") or att.get("name", "unknown"),
                        "content_type": (
                            att.get("content_type") or
                            att.get("contentType") or
                            att.get("type") or
                            "application/octet-stream"
                        ),
                        "content": att.get("content") or att.get("body") or att.get("data"),
                        "size": att.get("size", 0)
                    })
        
        return {
            "from": from_email,
            "to": to_email,
            "subject": subject,
            "attachments": attachments
        }
