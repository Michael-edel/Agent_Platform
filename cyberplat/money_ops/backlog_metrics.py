"""Периодическое обновление backlog метрик для Money Ops."""

import logging
from typing import Optional

from cyberplat.integrations.integration_job_service import IntegrationJobService
from cyberplat.payments.payment_service import PaymentService
from cyberplat.reconciliation.reconciliation_service import ReconciliationService

logger = logging.getLogger(__name__)


def update_backlog_metrics() -> None:
    """
    Обновить backlog метрики для Money Ops.
    
    Вызывается периодически (например, каждые 30 секунд).
    """
    try:
        from cyberplat.observability.metrics import (
            money_ops_queue_backlog,
            payment_orders_backlog,
            reconciliation_unmatched_total,
            METRICS_ENABLED
        )
        
        if not METRICS_ENABLED:
            return
        
        # 1. OneC jobs backlog
        if money_ops_queue_backlog:
            try:
                job_service = IntegrationJobService()
                conn = job_service._get_connection()
                cur = conn.cursor()
                
                # Считаем pending/processing jobs для 1С
                cur.execute(
                    """
                    SELECT COUNT(*) as cnt FROM integration_jobs
                    WHERE provider = 'onec' AND status IN ('pending', 'processing')
                    """,
                )
                backlog_count = cur.fetchone()["cnt"]
                conn.close()
                
                money_ops_queue_backlog.labels(queue="onec_jobs").set(backlog_count)
            except Exception as e:
                logger.warning(f"Ошибка при обновлении onec_jobs backlog: {e}")
        
        # 2. Payment orders backlog
        if payment_orders_backlog:
            try:
                payment_service = PaymentService()
                conn = payment_service._get_connection()
                cur = conn.cursor()
                
                # Считаем pending_approval orders
                cur.execute(
                    """
                    SELECT COUNT(*) as cnt FROM payment_orders
                    WHERE status = 'pending_approval'
                    """,
                )
                backlog_count = cur.fetchone()["cnt"]
                conn.close()
                
                payment_orders_backlog.labels(status="pending_approval").set(backlog_count)
            except Exception as e:
                logger.warning(f"Ошибка при обновлении payment_orders backlog: {e}")
        
        # 3. Reconciliation unmatched
        if reconciliation_unmatched_total:
            try:
                reconciliation_service = ReconciliationService()
                conn = reconciliation_service._get_connection()
                cur = conn.cursor()
                
                # Считаем unmatched транзакции
                cur.execute(
                    """
                    SELECT COUNT(*) as cnt FROM bank_transactions
                    WHERE matched = 0 AND direction = 'out'
                    """,
                )
                unmatched_count = cur.fetchone()["cnt"]
                conn.close()
                
                reconciliation_unmatched_total.set(unmatched_count)
            except Exception as e:
                logger.warning(f"Ошибка при обновлении reconciliation_unmatched: {e}")
    
    except ImportError:
        # Метрики не доступны
        pass
    except Exception as e:
        logger.error(f"Ошибка при обновлении backlog метрик: {e}", exc_info=True)
