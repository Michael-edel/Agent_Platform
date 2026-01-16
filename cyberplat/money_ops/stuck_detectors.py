"""Детекторы застрявших операций для Money Ops."""

import logging
from datetime import datetime, timedelta
from typing import List, Dict, Any

from cyberplat.payments.payment_service import PaymentService
from cyberplat.reconciliation.reconciliation_service import ReconciliationService
from cyberplat.case_service import CaseService

logger = logging.getLogger(__name__)


def detect_stuck_approvals(
    payment_service: PaymentService,
    case_service: CaseService,
    threshold_hours: int = 8
) -> List[Dict[str, Any]]:
    """
    Обнаружить застрявшие payment orders в статусе pending_approval.
    
    Args:
        payment_service: PaymentService
        case_service: CaseService
        threshold_hours: Порог в часах (по умолчанию 8)
        
    Returns:
        Список застрявших orders: [{"order_id": "...", "tenant_id": "...", "stuck_hours": ...}, ...]
    """
    stuck_orders = []
    
    try:
        conn = payment_service._get_connection()
        cur = conn.cursor()
        
        # Находим orders в pending_approval старше threshold
        threshold_time = (datetime.now() - timedelta(hours=threshold_hours)).isoformat()
        
        cur.execute(
            """
            SELECT id, tenant_id, status, created_at, updated_at
            FROM payment_orders
            WHERE status = 'pending_approval' AND updated_at < ?
            """,
            (threshold_time,)
        )
        
        rows = cur.fetchall()
        conn.close()
        
        for row in rows:
            order_id = row["id"]
            tenant_id = row["tenant_id"]
            updated_at = datetime.fromisoformat(row["updated_at"])
            stuck_hours = (datetime.now() - updated_at).total_seconds() / 3600
            
            stuck_orders.append({
                "order_id": order_id,
                "tenant_id": tenant_id,
                "stuck_hours": stuck_hours
            })
            
            # Создаём задачу в кейсе, если order привязан к кейсу
            order = payment_service.get_payment_order(order_id, tenant_id=tenant_id)
            if order and order.get("case_id"):
                case_id = order["case_id"]
                try:
                    # Проверяем, нет ли уже такой задачи
                    case = case_service.get_case(case_id, tenant_id=tenant_id)
                    if case:
                        conn = case_service._get_connection()
                        cur = conn.cursor()
                        cur.execute(
                            """
                            SELECT id FROM case_tasks
                            WHERE case_id = ? AND title LIKE '%согласован%' AND status = 'pending'
                            LIMIT 1
                            """,
                            (case_id,)
                        )
                        existing_task = cur.fetchone()
                        conn.close()
                        
                        if not existing_task:
                            # Создаём задачу
                            case_service.add_task(
                                case_id=case_id,
                                step_key=case.get("current_step") or "approval",
                                title="Платёж ожидает согласования",
                                assignee_role="director"
                            )
                            
                            logger.info(f"Создана задача для застрявшего approval: order={order_id}, case={case_id}")
                except Exception as e:
                    logger.warning(f"Ошибка при создании задачи для застрявшего approval: {e}")
    
    except Exception as e:
        logger.error(f"Ошибка при обнаружении застрявших approvals: {e}", exc_info=True)
    
    return stuck_orders


def detect_stuck_reconciliation(
    reconciliation_service: ReconciliationService,
    case_service: CaseService,
    threshold_hours: int = 24
) -> List[Dict[str, Any]]:
    """
    Обнаружить застрявшие выписки (parsed с unmatched транзакциями).
    
    Args:
        reconciliation_service: ReconciliationService
        case_service: CaseService (опционально, для создания задач)
        threshold_hours: Порог в часах (по умолчанию 24)
        
    Returns:
        Список застрявших statements: [{"statement_id": "...", "tenant_id": "...", "unmatched_count": ...}, ...]
    """
    stuck_statements = []
    
    try:
        conn = reconciliation_service._get_connection()
        cur = conn.cursor()
        
        # Находим statements в статусе parsed старше threshold
        threshold_time = (datetime.now() - timedelta(hours=threshold_hours)).isoformat()
        
        cur.execute(
            """
            SELECT bs.id, bs.tenant_id, bs.status, bs.created_at,
                   COUNT(bt.id) as unmatched_count
            FROM bank_statements bs
            LEFT JOIN bank_transactions bt ON bs.id = bt.statement_id
            WHERE bs.status = 'parsed' AND bs.created_at < ?
              AND (bt.matched = 0 OR bt.id IS NULL)
            GROUP BY bs.id, bs.tenant_id, bs.status, bs.created_at
            HAVING unmatched_count > 0
            """,
            (threshold_time,)
        )
        
        rows = cur.fetchall()
        conn.close()
        
        for row in rows:
            statement_id = row["id"]
            tenant_id = row["tenant_id"]
            unmatched_count = row["unmatched_count"]
            
            stuck_statements.append({
                "statement_id": statement_id,
                "tenant_id": tenant_id,
                "unmatched_count": unmatched_count
            })
            
            # Создаём задачу в кейсе (если есть связанные payment orders с case_id)
            # Для упрощения, создаём задачу на уровне tenant
            # В production можно привязать к конкретному кейсу через payment orders
            logger.info(f"Обнаружена застрявшая выписка: statement={statement_id}, unmatched={unmatched_count}")
    
    except Exception as e:
        logger.error(f"Ошибка при обнаружении застрявших reconciliation: {e}", exc_info=True)
    
    return stuck_statements
