"""Подписчик на события для billing (usage metering)."""

import json
import logging
from typing import Optional, Dict, Any
from datetime import datetime

from cyberplat.billing_service import BillingService
from cyberplat.artifact_service import ArtifactService

logger = logging.getLogger(__name__)


class BillingSubscriber:
    """Подписчик на события для записи usage в billing."""
    
    def __init__(
        self,
        billing_service: BillingService,
        artifact_service: ArtifactService
    ):
        """
        Инициализировать подписчик.
        
        Args:
            billing_service: Сервис биллинга
            artifact_service: Сервис артефактов (для получения данных)
        """
        self.billing_service = billing_service
        self.artifact_service = artifact_service
    
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
        Обработать событие и записать usage.
        
        Args:
            event_id: ID события
            event_type: Тип события
            tenant_id: ID тенанта (из события, уже валидирован)
            artifact_id: ID артефакта
            payload: Дополнительные данные события
            created_at: Время создания события
        """
        # Валидация tenant_id (дополнительная проверка)
        if not tenant_id or tenant_id.strip() == "" or tenant_id == "string":
            logger.warning(f"Пропущено событие {event_type} (event_id={event_id}): некорректный tenant_id")
            return
        
        tenant_id = tenant_id.strip()
        
        try:
            # Определяем период (YYYY-MM) из created_at
            period = self._extract_period(created_at)
            
            # Обрабатываем разные типы событий
            metrics = self._map_event_to_metrics(event_type, artifact_id, payload)
            
            # Записываем usage для каждой метрики
            for metric, units in metrics:
                # Разрешаем тариф
                rate = self.billing_service.resolve_rate(tenant_id, metric)
                
                if not rate:
                    # Нет тарифа - пропускаем (бесплатно)
                    logger.debug(f"Нет тарифа для metric={metric}, пропускаем")
                    continue
                
                # Записываем usage (идемпотентно)
                # Используем составной event_id для уникальности каждой метрики из события
                unique_event_id = f"{event_id}:{metric}"
                self.billing_service.record_event_charge(
                    tenant_id=tenant_id,
                    event_id=unique_event_id,
                    artifact_id=artifact_id,
                    event_type=event_type,
                    metric=metric,
                    units=units,
                    unit_price_minor=rate["unit_price_minor"],
                    currency=rate["currency"],
                    period=period
                )
                
        except Exception as e:
            logger.error(
                f"Ошибка при обработке billing для события {event_type} (event_id={event_id}): {e}",
                exc_info=True
            )
            # Не падаем, продолжаем работу
    
    def _extract_period(self, created_at: str) -> str:
        """
        Извлечь период (YYYY-MM) из ISO timestamp.
        
        Args:
            created_at: ISO timestamp (например, "2026-01-15T10:30:00")
            
        Returns:
            Период в формате YYYY-MM
        """
        try:
            # Парсим ISO timestamp
            dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            return dt.strftime("%Y-%m")
        except Exception as e:
            logger.warning(f"Ошибка при парсинге created_at={created_at}: {e}, используем текущий период")
            return datetime.now().strftime("%Y-%m")
    
    def _map_event_to_metrics(
        self,
        event_type: str,
        artifact_id: Optional[str],
        payload: Optional[Dict[str, Any]]
    ) -> list:
        """
        Преобразовать событие в список метрик.
        
        Returns:
            Список кортежей (metric, units)
        """
        metrics = []
        
        if event_type == "artifact.created":
            # artifact.created + kind=document → document_upload
            kind = payload.get("kind") if payload else None
            
            # Если kind не в payload, получаем из артефакта
            if not kind and artifact_id:
                artifact = self.artifact_service.get_artifact(artifact_id)
                if artifact:
                    kind = artifact.get("kind")
            
            if kind == "document":
                metrics.append(("document_upload", 1.0))
        
        elif event_type == "document.extracted":
            # document.extracted → invoice_extracted
            metrics.append(("invoice_extracted", 1.0))
            
            # Дополнительно: page_processed если есть total_pages
            total_pages = None
            
            # Пытаемся получить из payload
            if payload:
                # Может быть в payload напрямую или через invoice_data
                total_pages = payload.get("total_pages")
            
            # Если нет в payload, получаем из артефакта
            if total_pages is None and artifact_id:
                artifact = self.artifact_service.get_artifact(artifact_id)
                if artifact:
                    data = artifact.get("data", {})
                    total_pages = data.get("total_pages")
                    # Может быть в pages[0] или на верхнем уровне
                    if total_pages is None and "pages" in data:
                        if isinstance(data["pages"], list) and len(data["pages"]) > 0:
                            total_pages = len(data["pages"])
            
            if total_pages and total_pages > 0:
                metrics.append(("page_processed", float(total_pages)))
        
        elif event_type == "payment.prepared":
            metrics.append(("payment_prepared", 1.0))
        
        elif event_type == "payment.ready":
            metrics.append(("payment_ready", 1.0))
        
        elif event_type == "payment.invalid":
            metrics.append(("payment_invalid", 1.0))
        
        return metrics
