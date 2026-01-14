"""SQLAlchemy-based repository implementations for product/UI layer."""

import uuid
import logging
import json
from typing import Optional, Dict, Any, List
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_, text

from cyberplat.product.domain.interfaces import (
    ArtifactStateRepository,
    ExportRepository,
    EmailOcrJobRepository
)
from cyberplat.product.infrastructure.models import ArtifactState, Export, EmailOcrJob

logger = logging.getLogger(__name__)


class ArtifactStateRepositoryImpl(ArtifactStateRepository):
    """SQLAlchemy-based repository implementation для artifact states."""
    
    def __init__(self, session: Session):
        """
        Args:
            session: SQLAlchemy session (injected via dependency)
        """
        self.session = session
    
    def get_state(
        self,
        tenant_id: str,
        artifact_id: str
    ) -> Optional[Dict[str, Any]]:
        """Получить состояние артефакта."""
        state = self.session.query(ArtifactState).filter(
            and_(
                ArtifactState.tenant_id == tenant_id,
                ArtifactState.artifact_id == artifact_id
            )
        ).first()
        
        if not state:
            return None
        
        return {
            "id": state.id,
            "tenant_id": state.tenant_id,
            "artifact_id": state.artifact_id,
            "ui_status": state.ui_status,
            "source_artifact_id": state.source_artifact_id,
            "error_code": state.error_code,
            "error_message": state.error_message,
            "confirmed_at": state.confirmed_at,
            "exported_at": state.exported_at,
            "export_target": state.export_target,
            "created_at": state.created_at,
            "updated_at": state.updated_at
        }
    
    def create_or_update_state(
        self,
        tenant_id: str,
        artifact_id: str,
        ui_status: str,
        source_artifact_id: Optional[str] = None,
        error_code: Optional[str] = None,
        error_message: Optional[str] = None
    ) -> str:
        """Создать или обновить состояние артефакта."""
        state = self.session.query(ArtifactState).filter(
            ArtifactState.artifact_id == artifact_id
        ).first()
        
        now = datetime.now().isoformat()
        
        if state:
            # Обновляем существующее (с проверкой tenant_id)
            if state.tenant_id != tenant_id:
                raise ValueError(f"Artifact {artifact_id} belongs to different tenant")
            
            state.ui_status = ui_status
            state.source_artifact_id = source_artifact_id
            state.error_code = error_code
            state.error_message = error_message
            state.updated_at = now
        else:
            # Создаём новое
            state_id = str(uuid.uuid4())
            state = ArtifactState(
                id=state_id,
                tenant_id=tenant_id,
                artifact_id=artifact_id,
                ui_status=ui_status,
                source_artifact_id=source_artifact_id,
                error_code=error_code,
                error_message=error_message,
                created_at=now,
                updated_at=now
            )
            self.session.add(state)
            state_id = state.id
        
        self.session.commit()
        return state.id
    
    def update_status(
        self,
        tenant_id: str,
        artifact_id: str,
        ui_status: str,
        error_code: Optional[str] = None,
        error_message: Optional[str] = None
    ) -> bool:
        """Обновить статус артефакта."""
        state = self.session.query(ArtifactState).filter(
            and_(
                ArtifactState.tenant_id == tenant_id,
                ArtifactState.artifact_id == artifact_id
            )
        ).first()
        
        if not state:
            return False
        
        state.ui_status = ui_status
        state.error_code = error_code
        state.error_message = error_message
        state.updated_at = datetime.now().isoformat()
        
        self.session.commit()
        return True
    
    def mark_confirmed(
        self,
        tenant_id: str,
        artifact_id: str
    ) -> bool:
        """Отметить артефакт как подтверждённый."""
        state = self.session.query(ArtifactState).filter(
            and_(
                ArtifactState.tenant_id == tenant_id,
                ArtifactState.artifact_id == artifact_id
            )
        ).first()
        
        if not state:
            return False
        
        now = datetime.now().isoformat()
        state.ui_status = "confirmed"
        state.confirmed_at = now
        state.updated_at = now
        
        self.session.commit()
        return True
    
    def mark_exported(
        self,
        tenant_id: str,
        artifact_id: str,
        export_target: str
    ) -> bool:
        """Отметить артефакт как экспортированный."""
        state = self.session.query(ArtifactState).filter(
            and_(
                ArtifactState.tenant_id == tenant_id,
                ArtifactState.artifact_id == artifact_id
            )
        ).first()
        
        if not state:
            return False
        
        now = datetime.now().isoformat()
        state.ui_status = "exported"
        state.exported_at = now
        state.export_target = export_target
        state.updated_at = now
        
        self.session.commit()
        return True
    
    def list_by_kind_and_status(
        self,
        tenant_id: str,
        kind: str,
        ui_status: Optional[str] = None,
        limit: int = 100,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        """
        Получить список артефактов по kind и статусу.
        
        Возвращает UI-проекции (без raw OCR JSON из artifacts.data).

        Важно про архитектуру:
        - artifacts/events остаются в legacy sqlite (ArtifactService/EventService)
        - product слой хранит UI-состояния в SQLAlchemy (artifact_states, exports, ...)

        Поэтому список строим от artifact_states + подтягиваем метаданные артефакта из ArtifactService.
        """
        from cyberplat.artifact_service import ArtifactService
        import os

        db_path = os.getenv("PLATFORM_DB_PATH", "platform.db")
        artifact_service = ArtifactService(db_path=db_path)

        # Базовая выборка states по tenant (+ ui_status, если задан)
        query = self.session.query(ArtifactState).filter(ArtifactState.tenant_id == tenant_id)
        if ui_status:
            query = query.filter(ArtifactState.ui_status == ui_status)

        # Порядок: показываем последние сверху (created_at хранится ISO string → лексикографический порядок ок).
        query = query.order_by(ArtifactState.created_at.desc())

        # Так как kind не хранится в artifact_states, фильтруем по kind после получения артефакта из legacy.
        # Чтобы сохранить limit/offset семантику, читаем states "окнами" и копим совпадения.
        results: List[Dict[str, Any]] = []
        scan_offset = offset
        scan_limit = max(limit, 1)
        max_scans = 10  # защита от бесконечного цикла при мусорных/битых данных

        try:
            for _ in range(max_scans):
                states = query.limit(scan_limit).offset(scan_offset).all()
                if not states:
                    break

                for state in states:
                    artifact = artifact_service.get_artifact(state.artifact_id)
                    if not artifact:
                        continue
                    if artifact.get("tenant_id") != tenant_id:
                        # Жёсткий tenant-safety: даже если state по ошибке есть, не показываем чужие артефакты.
                        continue
                    if artifact.get("kind") != kind:
                        continue

                    created_at = artifact.get("created_at") or state.created_at
                    results.append(
                        {
                            "id": artifact["id"],
                            "kind": artifact["kind"],
                            "source": artifact.get("source") or "unknown",
                            "tenant_id": tenant_id,
                            "created_at": created_at,
                            "state": {
                                "ui_status": state.ui_status or "pending",
                                "source_artifact_id": state.source_artifact_id,
                                "error_code": state.error_code,
                                "error_message": state.error_message,
                                "confirmed_at": state.confirmed_at,
                                "exported_at": state.exported_at,
                                "export_target": state.export_target,
                                "updated_at": state.updated_at or created_at,
                            },
                        }
                    )

                    if len(results) >= limit:
                        return results

                scan_offset += len(states)

            return results
        finally:
            # Не держим keeper connection, но корректно закрываем, если он был создан (":memory:" режим тестов).
            try:
                artifact_service.close()
            except Exception:
                pass


class ExportRepositoryImpl(ExportRepository):
    """SQLAlchemy-based repository implementation для exports."""
    
    def __init__(self, session: Session):
        """
        Args:
            session: SQLAlchemy session (injected via dependency)
        """
        self.session = session
    
    def create_export(
        self,
        tenant_id: str,
        artifact_id: str,
        export_type: str,
        export_config: Optional[Dict[str, Any]] = None
    ) -> str:
        """Создать запись об экспорте."""
        export_id = str(uuid.uuid4())
        now = datetime.now().isoformat()
        
        export = Export(
            id=export_id,
            tenant_id=tenant_id,
            artifact_id=artifact_id,
            export_type=export_type,
            export_config=json.dumps(export_config) if export_config else None,
            status="pending",
            created_at=now
        )
        
        self.session.add(export)
        self.session.commit()
        
        return export_id
    
    def update_export(
        self,
        export_id: str,
        status: str,
        file_id: Optional[str] = None,
        file_path: Optional[str] = None,
        error_message: Optional[str] = None
    ) -> bool:
        """Обновить статус экспорта."""
        export = self.session.query(Export).filter(Export.id == export_id).first()
        
        if not export:
            return False
        
        export.status = status
        export.file_id = file_id
        export.file_path = file_path
        export.error_message = error_message
        export.completed_at = datetime.now().isoformat() if status == "completed" else None
        
        self.session.commit()
        return True
    
    def get_export(
        self,
        tenant_id: str,
        export_id: str
    ) -> Optional[Dict[str, Any]]:
        """Получить экспорт по ID."""
        export = self.session.query(Export).filter(
            and_(
                Export.id == export_id,
                Export.tenant_id == tenant_id
            )
        ).first()
        
        if not export:
            return None
        
        return {
            "id": export.id,
            "tenant_id": export.tenant_id,
            "artifact_id": export.artifact_id,
            "export_type": export.export_type,
            "file_id": export.file_id,
            "file_path": export.file_path,
            "export_config": json.loads(export.export_config) if export.export_config else None,
            "status": export.status,
            "error_message": export.error_message,
            "created_at": export.created_at,
            "completed_at": export.completed_at
        }
    
    def list_exports(
        self,
        tenant_id: str,
        artifact_id: Optional[str] = None,
        export_type: Optional[str] = None,
        limit: int = 100,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        """Получить список экспортов."""
        query = self.session.query(Export).filter(Export.tenant_id == tenant_id)
        
        if artifact_id:
            query = query.filter(Export.artifact_id == artifact_id)
        
        if export_type:
            query = query.filter(Export.export_type == export_type)
        
        exports = query.order_by(Export.created_at.desc()).limit(limit).offset(offset).all()
        
        results = []
        for export in exports:
            results.append({
                "id": export.id,
                "tenant_id": export.tenant_id,
                "artifact_id": export.artifact_id,
                "export_type": export.export_type,
                "file_id": export.file_id,
                "file_path": export.file_path,
                "export_config": json.loads(export.export_config) if export.export_config else None,
                "status": export.status,
                "error_message": export.error_message,
                "created_at": export.created_at,
                "completed_at": export.completed_at
            })
        
        return results


class EmailOcrJobRepositoryImpl(EmailOcrJobRepository):
    """SQLAlchemy-based repository implementation для email OCR jobs."""
    
    def __init__(self, session: Session):
        """
        Args:
            session: SQLAlchemy session (injected via dependency)
        """
        self.session = session
    
    def create_job(
        self,
        tenant_id: str,
        document_artifact_id: str,
        idempotency_key: str,
        max_retries: int = 5,
        email_from: Optional[str] = None,
        email_to: Optional[str] = None,
        email_subject: Optional[str] = None,
        attachment_filename: Optional[str] = None,
        attachment_size: Optional[int] = None
    ) -> str:
        """
        Создать job (или вернуть существующий по idempotency_key).
        
        Args:
            tenant_id: ID тенанта
            document_artifact_id: ID document artifact
            idempotency_key: Idempotency key
            max_retries: Максимальное количество попыток
            email_from: Email отправителя (для inbox)
            email_to: Email получателя (для inbox)
            email_subject: Тема письма (для inbox)
            attachment_filename: Имя файла вложения (для inbox)
            attachment_size: Размер вложения в байтах (для inbox)
        
        Returns:
            job_id (существующего или нового job)
        """
        # Проверяем, существует ли job с таким idempotency_key
        existing_job = self.session.query(EmailOcrJob).filter(
            EmailOcrJob.idempotency_key == idempotency_key
        ).first()
        
        if existing_job:
            # Если job уже done, не создаём новый
            if existing_job.status == "done":
                logger.info(f"Job already done for idempotency_key: {idempotency_key[:16]}...")
                return existing_job.id
            
            # Если job failed/dead, можно обновить его на queued (но это зависит от логики)
            # Для MVP просто возвращаем существующий job_id
            logger.info(f"Job already exists for idempotency_key: {idempotency_key[:16]}... (status={existing_job.status})")
            return existing_job.id
        
        # Создаём новый job
        job_id = str(uuid.uuid4())
        now = datetime.now().isoformat()
        
        job = EmailOcrJob(
            id=job_id,
            tenant_id=tenant_id,
            document_artifact_id=document_artifact_id,
            idempotency_key=idempotency_key,
            status="queued",
            attempts=0,
            max_retries=max_retries,
            next_run_at=now,  # Готов к немедленной обработке
            email_from=email_from,
            email_to=email_to,
            email_subject=email_subject,
            attachment_filename=attachment_filename,
            attachment_size=attachment_size,
            created_at=now,
            updated_at=now
        )
        
        self.session.add(job)
        self.session.commit()
        
        logger.info(f"Created email OCR job: {job_id} (document={document_artifact_id}, tenant={tenant_id})")
        return job_id
    
    def get_job_by_idempotency_key(
        self,
        idempotency_key: str
    ) -> Optional[Dict[str, Any]]:
        """Получить job по idempotency_key."""
        job = self.session.query(EmailOcrJob).filter(
            EmailOcrJob.idempotency_key == idempotency_key
        ).first()
        
        if not job:
            return None
        
        return self._job_to_dict(job)
    
    def claim_job_for_processing(
        self,
        job_id: str
    ) -> bool:
        """
        Атомарно "забрать" job для обработки (status: queued/failed → processing).
        
        Returns:
            True если job успешно забран, False если уже обрабатывается или done/dead
        """
        job = self.session.query(EmailOcrJob).filter(EmailOcrJob.id == job_id).first()
        
        if not job:
            return False
        
        # Проверяем, можно ли забрать job
        if job.status not in ("queued", "failed"):
            return False
        
        # Проверяем, что next_run_at <= now
        if job.next_run_at:
            try:
                next_run_dt = datetime.fromisoformat(job.next_run_at)
                if next_run_dt > datetime.now():
                    return False
            except Exception:
                # Если не удалось распарсить, считаем что можно обработать
                pass
        
        # Атомарно обновляем статус
        job.status = "processing"
        job.updated_at = datetime.now().isoformat()
        
        try:
            self.session.commit()
            return True
        except Exception as e:
            # Возможно, race condition (другой процесс уже забрал)
            self.session.rollback()
            logger.warning(f"Failed to claim job {job_id}: {e}")
            return False
    
    def mark_job_done(
        self,
        job_id: str,
        invoice_artifact_id: Optional[str] = None
    ) -> bool:
        """Отметить job как выполненный."""
        job = self.session.query(EmailOcrJob).filter(EmailOcrJob.id == job_id).first()
        
        if not job:
            return False
        
        job.status = "done"
        job.invoice_artifact_id = invoice_artifact_id
        job.updated_at = datetime.now().isoformat()
        
        self.session.commit()
        return True
    
    def mark_job_failed(
        self,
        job_id: str,
        error_message: str,
        next_run_at: str,
        attempts: int
    ) -> bool:
        """Отметить job как failed и запланировать retry."""
        job = self.session.query(EmailOcrJob).filter(EmailOcrJob.id == job_id).first()
        
        if not job:
            return False
        
        job.status = "failed"
        job.last_error = error_message[:500] if error_message else None  # Ограничиваем длину
        job.next_run_at = next_run_at
        job.attempts = attempts
        job.updated_at = datetime.now().isoformat()
        
        self.session.commit()
        return True
    
    def mark_job_dead(
        self,
        job_id: str,
        error_message: str
    ) -> bool:
        """Отметить job как dead (превышен max_retries)."""
        job = self.session.query(EmailOcrJob).filter(EmailOcrJob.id == job_id).first()
        
        if not job:
            return False
        
        job.status = "dead"
        # Важно: при переводе в dead увеличиваем attempts (фиксирует финальную попытку).
        try:
            job.attempts = int(job.attempts or 0) + 1
        except Exception:
            job.attempts = 1
        job.last_error = error_message[:500] if error_message else None
        job.updated_at = datetime.now().isoformat()
        
        self.session.commit()
        return True
    
    def get_jobs_for_processing(
        self,
        limit: int = 10
    ) -> List[Dict[str, Any]]:
        """
        Получить jobs готовые к обработке (status in (queued, failed) AND next_run_at <= now).
        
        Returns:
            List of jobs ordered by next_run_at ASC
        """
        now = datetime.now().isoformat()
        
        # Используем raw SQL для эффективного запроса с условием на timestamp
        query = text("""
            SELECT * FROM email_ocr_jobs
            WHERE status IN ('queued', 'failed')
            AND (next_run_at IS NULL OR next_run_at <= :now)
            ORDER BY next_run_at ASC NULLS FIRST
            LIMIT :limit
        """)
        
        result = self.session.execute(query, {"now": now, "limit": limit})
        rows = result.fetchall()
        
        jobs = []
        for row in rows:
            # Преобразуем Row в dict
            job_dict = {
                "id": row.id,
                "tenant_id": row.tenant_id,
                "document_artifact_id": row.document_artifact_id,
                "idempotency_key": row.idempotency_key,
                "status": row.status,
                "attempts": row.attempts,
                "max_retries": row.max_retries,
                "next_run_at": row.next_run_at,
                "last_error": row.last_error,
                "invoice_artifact_id": row.invoice_artifact_id,
                "email_from": getattr(row, 'email_from', None),
                "email_to": getattr(row, 'email_to', None),
                "email_subject": getattr(row, 'email_subject', None),
                "attachment_filename": getattr(row, 'attachment_filename', None),
                "attachment_size": getattr(row, 'attachment_size', None),
                "created_at": row.created_at,
                "updated_at": row.updated_at
            }
            jobs.append(job_dict)
        
        return jobs
    
    def get_job(
        self,
        job_id: str
    ) -> Optional[Dict[str, Any]]:
        """Получить job по ID."""
        job = self.session.query(EmailOcrJob).filter(EmailOcrJob.id == job_id).first()
        
        if not job:
            return None
        
        return self._job_to_dict(job)
    
    def _job_to_dict(self, job: EmailOcrJob) -> Dict[str, Any]:
        """Преобразовать EmailOcrJob в dict."""
        return {
            "id": job.id,
            "tenant_id": job.tenant_id,
            "document_artifact_id": job.document_artifact_id,
            "idempotency_key": job.idempotency_key,
            "status": job.status,
            "attempts": job.attempts,
            "max_retries": job.max_retries,
            "next_run_at": job.next_run_at,
            "last_error": job.last_error,
            "invoice_artifact_id": job.invoice_artifact_id,
            "email_from": job.email_from,
            "email_to": job.email_to,
            "email_subject": job.email_subject,
            "attachment_filename": job.attachment_filename,
            "attachment_size": job.attachment_size,
            "created_at": job.created_at,
            "updated_at": job.updated_at
        }
    
    def list_inbox_emails(
        self,
        tenant_id: str,
        limit: int = 50,
        cursor: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Получить список email jobs для inbox (tenant-scoped).
        
        Args:
            tenant_id: ID тенанта
            limit: Максимальное количество записей
            cursor: ISO timestamp + id для pagination (опционально)
            
        Returns:
            List of jobs ordered by created_at DESC
        """
        query = self.session.query(EmailOcrJob).filter(
            EmailOcrJob.tenant_id == tenant_id
        )
        
        # Cursor pagination (если передан)
        if cursor:
            try:
                # Формат cursor: "2026-01-14T23:00:00|job-id"
                cursor_timestamp, cursor_id = cursor.split("|", 1)
                # Выбираем записи где created_at < cursor_timestamp или (created_at = cursor_timestamp и id < cursor_id)
                query = query.filter(
                    or_(
                        EmailOcrJob.created_at < cursor_timestamp,
                        and_(
                            EmailOcrJob.created_at == cursor_timestamp,
                            EmailOcrJob.id < cursor_id
                        )
                    )
                )
            except Exception:
                # Если cursor невалиден, игнорируем
                logger.warning(f"Invalid cursor format: {cursor}")
        
        # Сортируем по created_at DESC (последние первыми)
        jobs = query.order_by(
            EmailOcrJob.created_at.desc(),
            EmailOcrJob.id.desc()
        ).limit(limit).all()
        
        results = []
        for job in jobs:
            results.append(self._job_to_dict(job))
        
        return results

    def get_stuck_processing_jobs(
        self,
        timeout_seconds: int
    ) -> List[Dict[str, Any]]:
        """
        Получить jobs со status=processing, которые "залипли" (updated_at слишком старый).

        Важно: updated_at хранится как ISO string, поэтому сравнение по строке корректно
        при формате datetime.isoformat().
        """
        cutoff = (datetime.now() - timedelta(seconds=timeout_seconds)).isoformat()
        jobs = (
            self.session.query(EmailOcrJob)
            .filter(EmailOcrJob.status == "processing")
            .filter(EmailOcrJob.updated_at != None)  # noqa: E711
            .filter(EmailOcrJob.updated_at < cutoff)
            .order_by(EmailOcrJob.updated_at.asc())
            .all()
        )
        return [self._job_to_dict(j) for j in jobs]

    def count_processing_jobs(self) -> int:
        """Подсчитать количество jobs со status=processing (для concurrency limit)."""
        return int(
            self.session.query(EmailOcrJob)
            .filter(EmailOcrJob.status == "processing")
            .count()
        )
