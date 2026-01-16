"""API роутер для работы с банковскими выписками и сверкой."""

import logging
import tempfile
import os
from typing import Optional, List
from pathlib import Path
from fastapi import APIRouter, HTTPException, Header, Depends, UploadFile, File
from pydantic import BaseModel
from datetime import date

from cyberplat.reconciliation.reconciliation_service import ReconciliationService, ReconciliationError
from cyberplat.payments.payment_service import PaymentService
from cyberplat.case_service import CaseService
from cyberplat.artifact_service import ArtifactService

logger = logging.getLogger(__name__)

router = APIRouter()


# Pydantic модели
class StatementResponse(BaseModel):
    id: str
    tenant_id: str
    source: str
    period_start: Optional[str]
    period_end: Optional[str]
    status: str
    created_at: str
    updated_at: str


class TransactionResponse(BaseModel):
    id: str
    statement_id: str
    tenant_id: str
    txn_date: str
    amount: float
    currency: str
    counterparty_name: Optional[str]
    counterparty_account: Optional[str]
    description: Optional[str]
    direction: str
    matched: bool
    created_at: str


class TransactionsListResponse(BaseModel):
    items: List[TransactionResponse]
    total: int


class ManualMatchRequest(BaseModel):
    transaction_id: str
    payment_order_id: str


class ManualMatchResponse(BaseModel):
    success: bool
    message: str
    match_id: str


class AutoMatchResponse(BaseModel):
    success: bool
    message: str
    matched_count: int
    unmatched_count: int


class FinalizeResponse(BaseModel):
    success: bool
    message: str


class UploadResponse(BaseModel):
    success: bool
    message: str
    statement_id: str
    transactions_count: int


class BankStatementImportResponse(BaseModel):
    success: bool
    message: str
    imported_count: int
    skipped_count: int
    statement_line_ids: List[str]


class SuggestionItem(BaseModel):
    statement_line_id: str
    date: str
    amount: float
    confidence: str  # low|medium


class SuggestionsResponse(BaseModel):
    payment_id: str
    suggestions: List[SuggestionItem]


# Dependencies
def get_reconciliation_service() -> ReconciliationService:
    """Получить экземпляр ReconciliationService."""
    return ReconciliationService()


def get_payment_service() -> PaymentService:
    """Получить экземпляр PaymentService."""
    return PaymentService()


def get_case_service() -> CaseService:
    """Получить экземпляр CaseService."""
    return CaseService()


def get_artifact_service() -> ArtifactService:
    """Получить экземпляр ArtifactService."""
    return ArtifactService()


@router.post("/reconciliation/bank-statements/import", response_model=BankStatementImportResponse, status_code=201)
async def import_bank_statement_lines(
    file: UploadFile = File(...),
    x_tenant_id: Optional[str] = Header(None, alias="X-Tenant-ID"),
    artifact_service: ArtifactService = Depends(get_artifact_service),
):
    """
    Импорт выписки (CSV) как артефакты bank.statement.line.

    Формат CSV (фиксированный):
    date,amount,description
    2026-01-15,1000.00,Payment INV-123

    Важно:
    - tenant-scoped
    - не делает матчинга и не меняет платежи автоматически
    """
    if not x_tenant_id:
        raise HTTPException(status_code=400, detail="X-Tenant-ID header обязателен")
    if not file.filename.endswith(".csv"):
        raise HTTPException(status_code=400, detail="Поддерживается только формат CSV")

    import csv
    from datetime import datetime as _dt

    content = (await file.read()).decode("utf-8", errors="replace")
    reader = csv.reader(content.splitlines())
    rows = list(reader)
    if not rows:
        raise HTTPException(status_code=400, detail="Пустой CSV")

    header = [h.strip() for h in rows[0]]
    if header[:3] != ["date", "amount", "description"]:
        raise HTTPException(status_code=400, detail="Неверный формат CSV: ожидается header date,amount,description")

    imported = 0
    skipped = 0
    ids: List[str] = []
    for row in rows[1:]:
        try:
            if len(row) < 3:
                skipped += 1
                continue
            date_str = row[0].strip()
            amount_str = row[1].strip()
            desc = row[2].strip()
            _dt.strptime(date_str, "%Y-%m-%d")
            amount = float(amount_str)
            raw_line = ",".join(row)
            artifact_id = artifact_service.create_artifact(
                kind="bank.statement.line",
                source="bank_statement_import",
                tenant_id=x_tenant_id,
                data={
                    "date": date_str,
                    "amount": amount,
                    "description": desc,
                    "raw_line": raw_line,
                },
            )
            ids.append(artifact_id)
            imported += 1
        except Exception:
            skipped += 1
            continue

    # Метрики
    try:
        from cyberplat.observability.metrics import bank_statement_lines_imported_total, METRICS_ENABLED
        if METRICS_ENABLED and bank_statement_lines_imported_total:
            bank_statement_lines_imported_total.inc(imported)
    except Exception:
        pass

    return BankStatementImportResponse(
        success=True,
        message="Импорт строк выписки завершён",
        imported_count=imported,
        skipped_count=skipped,
        statement_line_ids=ids,
    )


@router.get("/reconciliation/suggestions", response_model=SuggestionsResponse)
async def get_reconciliation_suggestions(
    payment_id: str,
    days: int = 3,
    x_tenant_id: Optional[str] = Header(None, alias="X-Tenant-ID"),
    payment_service: PaymentService = Depends(get_payment_service),
    artifact_service: ArtifactService = Depends(get_artifact_service),
):
    """Read-only suggestions for reconciliation (no automatic matching)."""
    if not x_tenant_id:
        raise HTTPException(status_code=400, detail="X-Tenant-ID header обязателен")
    if days < 0 or days > 31:
        raise HTTPException(status_code=400, detail="days должен быть в диапазоне 0..31")

    order = payment_service.get_payment_order(payment_id, tenant_id=x_tenant_id)
    if not order:
        raise HTTPException(status_code=404, detail="Платёжное поручение не найдено")

    from datetime import datetime as _dt
    from datetime import timedelta as _td

    def _parse_iso_date(value: Optional[str]) -> Optional[date]:
        if not value:
            return None
        raw = str(value)
        try:
            if raw.endswith("Z"):
                raw = raw[:-1] + "+00:00"
            return _dt.fromisoformat(raw).date()
        except Exception:
            try:
                return _dt.strptime(raw[:10], "%Y-%m-%d").date()
            except Exception:
                return None

    ref_date = _parse_iso_date(order.get("approved_at")) or _parse_iso_date(order.get("created_at"))
    if not ref_date:
        ref_date = date.today()

    payment_amount = float(order["amount"])
    artifacts = artifact_service.list_artifacts(kind="bank.statement.line", tenant_id=x_tenant_id, limit=5000)

    suggestions: List[SuggestionItem] = []
    for a in artifacts:
        data = a.get("data") or {}
        try:
            line_amount = float(data.get("amount"))
            line_date_str = str(data.get("date") or "")
            line_date = _dt.strptime(line_date_str, "%Y-%m-%d").date()
        except Exception:
            continue

        if abs(line_amount - payment_amount) > 0.01:
            continue
        delta = abs((line_date - ref_date).days)
        if delta > days:
            continue
        confidence = "medium" if delta == 0 else "low"
        suggestions.append(
            SuggestionItem(
                statement_line_id=a["id"],
                date=line_date_str,
                amount=line_amount,
                confidence=confidence,
            )
        )

    # Sort by closest date, then newest
    suggestions.sort(key=lambda s: (abs((_dt.strptime(s.date, "%Y-%m-%d").date() - ref_date).days), s.statement_line_id))
    return SuggestionsResponse(payment_id=payment_id, suggestions=suggestions[:50])

@router.post("/reconciliation/statements/upload", response_model=UploadResponse, status_code=201)
async def upload_statement(
    file: UploadFile = File(...),
    x_tenant_id: Optional[str] = Header(None, alias="X-Tenant-ID"),
    reconciliation_service: ReconciliationService = Depends(get_reconciliation_service)
):
    """
    Загрузить банковскую выписку (CSV).
    
    Требует заголовок X-Tenant-ID.
    Формат CSV: date, amount, description, counterparty
    """
    if not x_tenant_id:
        raise HTTPException(status_code=400, detail="X-Tenant-ID header обязателен")
    
    if not file.filename.endswith('.csv'):
        raise HTTPException(status_code=400, detail="Поддерживается только формат CSV")
    
    try:
        # Создаём выписку
        statement_id = reconciliation_service.ingest_statement(
            tenant_id=x_tenant_id,
            source="upload"
        )
        
        # Сохраняем файл во временную директорию
        temp_dir = Path("out/uploads")
        temp_dir.mkdir(parents=True, exist_ok=True)
        
        file_path = temp_dir / f"statement_{statement_id}.csv"
        
        with open(file_path, "wb") as f:
            content = await file.read()
            f.write(content)
        
        # Парсим транзакции
        transactions_count = reconciliation_service.parse_transactions_csv(
            statement_id=statement_id,
            tenant_id=x_tenant_id,
            file_path=str(file_path)
        )
        
        logger.info(f"Выписка загружена: {statement_id}, транзакций: {transactions_count}")
        
        return UploadResponse(
            success=True,
            message=f"Выписка загружена и обработана",
            statement_id=statement_id,
            transactions_count=transactions_count
        )
    except ReconciliationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Ошибка при загрузке выписки: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Ошибка при загрузке выписки: {str(e)}")


@router.post("/reconciliation/statements/{statement_id}/auto-match", response_model=AutoMatchResponse, status_code=200)
async def auto_match_statement(
    statement_id: str,
    x_tenant_id: Optional[str] = Header(None, alias="X-Tenant-ID"),
    reconciliation_service: ReconciliationService = Depends(get_reconciliation_service),
    payment_service: PaymentService = Depends(get_payment_service)
):
    """
    Автоматическое сопоставление транзакций выписки с платёжными поручениями.
    
    Требует заголовок X-Tenant-ID.
    """
    if not x_tenant_id:
        raise HTTPException(status_code=400, detail="X-Tenant-ID header обязателен")
    
    try:
        match_result = reconciliation_service.auto_match(
            statement_id=statement_id,
            tenant_id=x_tenant_id,
            payment_service=payment_service
        )
        
        # Метрики
        try:
            from cyberplat.observability.metrics import (
                reconciliation_transactions_total,
                reconciliation_auto_match_rate,
                METRICS_ENABLED
            )
            if METRICS_ENABLED:
                if reconciliation_transactions_total:
                    reconciliation_transactions_total.labels(matched="true").inc(match_result["matched_count"])
                    reconciliation_transactions_total.labels(matched="false").inc(match_result["unmatched_count"])
                if reconciliation_auto_match_rate and match_result["matched_count"] + match_result["unmatched_count"] > 0:
                    rate = match_result["matched_count"] / (match_result["matched_count"] + match_result["unmatched_count"])
                    reconciliation_auto_match_rate.observe(rate)
        except Exception:
            pass
        
        return AutoMatchResponse(
            success=True,
            message="Автосопоставление завершено",
            matched_count=match_result["matched_count"],
            unmatched_count=match_result["unmatched_count"]
        )
    except Exception as e:
        logger.error(f"Ошибка при автосопоставлении: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Ошибка при автосопоставлении: {str(e)}")


@router.get("/reconciliation/statements/{statement_id}", response_model=StatementResponse)
async def get_statement(
    statement_id: str,
    x_tenant_id: Optional[str] = Header(None, alias="X-Tenant-ID"),
    reconciliation_service: ReconciliationService = Depends(get_reconciliation_service)
):
    """
    Получить выписку по ID.
    
    Требует заголовок X-Tenant-ID.
    """
    if not x_tenant_id:
        raise HTTPException(status_code=400, detail="X-Tenant-ID header обязателен")
    
    try:
        statement = reconciliation_service.get_statement(statement_id, tenant_id=x_tenant_id)
        if not statement:
            raise HTTPException(status_code=404, detail="Выписка не найдена")
        return StatementResponse(**statement)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка при получении выписки: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Ошибка при получении выписки: {str(e)}")


@router.get("/reconciliation/statements/{statement_id}/transactions", response_model=TransactionsListResponse)
async def get_transactions(
    statement_id: str,
    matched: Optional[bool] = None,
    x_tenant_id: Optional[str] = Header(None, alias="X-Tenant-ID"),
    reconciliation_service: ReconciliationService = Depends(get_reconciliation_service)
):
    """
    Получить транзакции выписки.
    
    Требует заголовок X-Tenant-ID.
    Параметр matched: true/false для фильтрации.
    """
    if not x_tenant_id:
        raise HTTPException(status_code=400, detail="X-Tenant-ID header обязателен")
    
    try:
        transactions = reconciliation_service.get_transactions(
            statement_id=statement_id,
            tenant_id=x_tenant_id,
            matched=matched
        )
        return TransactionsListResponse(
            items=[TransactionResponse(**txn) for txn in transactions],
            total=len(transactions)
        )
    except Exception as e:
        logger.error(f"Ошибка при получении транзакций: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Ошибка при получении транзакций: {str(e)}")


@router.post("/reconciliation/match", response_model=ManualMatchResponse, status_code=200)
async def manual_match(
    request: ManualMatchRequest,
    x_tenant_id: Optional[str] = Header(None, alias="X-Tenant-ID"),
    reconciliation_service: ReconciliationService = Depends(get_reconciliation_service),
    payment_service: PaymentService = Depends(get_payment_service),
    case_service: CaseService = Depends(get_case_service)
):
    """
    Ручное сопоставление транзакции с платёжным поручением.
    
    Требует заголовок X-Tenant-ID.
    """
    if not x_tenant_id:
        raise HTTPException(status_code=400, detail="X-Tenant-ID header обязателен")
    
    try:
        # Проверяем, что payment_order существует и принадлежит tenant
        payment_order = payment_service.get_payment_order(request.payment_order_id, tenant_id=x_tenant_id)
        if not payment_order:
            raise HTTPException(status_code=404, detail="Платёжное поручение не найдено")
        
        # Создаём match
        match_id = reconciliation_service.manual_match(
            tenant_id=x_tenant_id,
            transaction_id=request.transaction_id,
            payment_order_id=request.payment_order_id
        )
        
        # Интеграция с Case/Payments
        if payment_order.get("case_id"):
            case_id = payment_order["case_id"]
            try:
                # Закрываем задачу "Ожидается оплата" если есть
                conn = case_service._get_connection()
                cur = conn.cursor()
                cur.execute(
                    """
                    SELECT id FROM case_tasks
                    WHERE case_id = ? AND (title LIKE '%оплат%' OR title LIKE '%payment%') AND status = 'pending'
                    LIMIT 1
                    """,
                    (case_id,)
                )
                task = cur.fetchone()
                if task:
                    case_service.complete_task(case_id, task["id"])
                
                # Логируем событие
                case_service._log_event(
                    conn,
                    case_id,
                    "payment.reconciled",
                    {
                        "transaction_id": request.transaction_id,
                        "payment_order_id": request.payment_order_id,
                        "amount": payment_order["amount"]
                    }
                )
                conn.commit()
                conn.close()
            except Exception as e:
                logger.warning(f"Ошибка при интеграции с кейсом: {e}")
        
        # Метрики
        try:
            from cyberplat.observability.metrics import reconciliation_transactions_total, METRICS_ENABLED
            if METRICS_ENABLED and reconciliation_transactions_total:
                reconciliation_transactions_total.labels(matched="true").inc()
        except Exception:
            pass
        
        return ManualMatchResponse(
            success=True,
            message="Транзакция сопоставлена с платёжным поручением",
            match_id=match_id
        )
    except ReconciliationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка при сопоставлении: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Ошибка при сопоставлении: {str(e)}")


@router.post("/reconciliation/statements/{statement_id}/finalize", response_model=FinalizeResponse, status_code=200)
async def finalize_statement(
    statement_id: str,
    x_tenant_id: Optional[str] = Header(None, alias="X-Tenant-ID"),
    reconciliation_service: ReconciliationService = Depends(get_reconciliation_service)
):
    """
    Завершить обработку выписки (пометить как reconciled).
    
    Требует заголовок X-Tenant-ID.
    """
    if not x_tenant_id:
        raise HTTPException(status_code=400, detail="X-Tenant-ID header обязателен")
    
    try:
        reconciliation_service.finalize_statement(statement_id, x_tenant_id)
        
        return FinalizeResponse(
            success=True,
            message="Выписка завершена"
        )
    except Exception as e:
        logger.error(f"Ошибка при завершении выписки: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Ошибка при завершении выписки: {str(e)}")
