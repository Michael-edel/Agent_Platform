"""Use case: Process email OCR jobs (auto-OCR after email ingestion)."""

import logging
import os
import random
import math
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


class ProcessEmailOcrJobsUseCase:
    """Use case для обработки email OCR jobs с идемпотентностью и ретраями."""
    
    def __init__(
        self,
        email_ocr_job_repo,  # EmailOcrJobRepository
        artifact_state_repo,  # ArtifactStateRepository
        artifact_service,  # ArtifactService (legacy)
        doc_agent,  # DocAgent
        event_service,  # EventService
        billing_enforcement=None,  # BillingEnforcementService (optional, для проверки квот)
        max_retries: int = 5,
        retry_base_seconds: int = 10,
        retry_max_seconds: int = 600,
        processing_timeout_seconds: int = 900,
        max_concurrent_jobs: int = 3
    ):
        self.email_ocr_job_repo = email_ocr_job_repo
        self.artifact_state_repo = artifact_state_repo
        self.artifact_service = artifact_service
        self.doc_agent = doc_agent
        self.event_service = event_service
        self.billing_enforcement = billing_enforcement
        self.max_retries = max_retries
        self.retry_base_seconds = retry_base_seconds
        self.retry_max_seconds = retry_max_seconds
        self.processing_timeout_seconds = processing_timeout_seconds
        self.max_concurrent_jobs = max_concurrent_jobs
    
    def execute(
        self,
        limit: int = 10
    ) -> Dict[str, Any]:
        """
        Обработать готовые к обработке email OCR jobs.
        
        Args:
            limit: Максимальное количество jobs для обработки за один вызов
            
        Returns:
            {
                "processed": int,
                "succeeded": int,
                "failed": int,
                "dead": int,
                "skipped_due_to_limit": int,
                "errors": List[str]
            }
        """
        # ШАГ 1: Recovery stuck processing jobs (ДО выбора новых jobs)
        recovered = self._recover_stuck_jobs()
        logger.info(f"Recovered {recovered} stuck processing jobs")
        
        # ШАГ 2: Проверяем concurrency limit
        current_processing = self.email_ocr_job_repo.count_processing_jobs()
        available_slots = max(0, self.max_concurrent_jobs - current_processing)
        
        # ШАГ 3: Получаем jobs готовые к обработке
        all_ready_jobs = self.email_ocr_job_repo.get_jobs_for_processing(limit=limit * 2)  # Получаем больше для подсчёта skipped
        
        if available_slots <= 0:
            logger.warning(
                f"Concurrency limit reached: {current_processing}/{self.max_concurrent_jobs} jobs processing"
            )
            # Все готовые jobs пропускаются из-за лимита
            return {
                "processed": 0,
                "succeeded": 0,
                "failed": 0,
                "dead": 0,
                "skipped_due_to_limit": len(all_ready_jobs),
                "errors": []
            }
        
        # Ограничиваем количество jobs доступными слотами
        jobs = all_ready_jobs[:available_slots]
        skipped_due_to_limit = len(all_ready_jobs) - len(jobs)
        
        if not jobs:
            logger.debug("No email OCR jobs ready for processing")
            return {
                "processed": 0,
                "succeeded": 0,
                "failed": 0,
                "dead": 0,
                "skipped_due_to_limit": skipped_due_to_limit if 'skipped_due_to_limit' in locals() else 0,
                "errors": []
            }
        
        logger.info(
            f"Processing {len(jobs)} email OCR jobs "
            f"(available_slots={available_slots}, skipped_due_to_limit={skipped_due_to_limit})"
        )
        
        processed = 0
        succeeded = 0
        failed = 0
        dead = 0
        errors = []
        
        for job in jobs:
            try:
                result = self._process_single_job(job)
                
                if result["status"] == "succeeded":
                    succeeded += 1
                elif result["status"] == "failed":
                    failed += 1
                elif result["status"] == "dead":
                    dead += 1
                elif result["status"] == "skipped":
                    # Job уже обрабатывается или done
                    continue
                
                processed += 1
                
                if result.get("error"):
                    errors.append(f"Job {job['id']}: {result['error']}")
                    
            except Exception as e:
                error_msg = f"Unexpected error processing job {job.get('id', 'unknown')}: {str(e)}"
                logger.error(error_msg, exc_info=True)
                errors.append(error_msg)
                failed += 1
        
        logger.info(
            f"Email OCR jobs processing completed: "
            f"processed={processed}, succeeded={succeeded}, failed={failed}, dead={dead}, "
            f"skipped_due_to_limit={skipped_due_to_limit}"
        )
        
        return {
            "processed": processed,
            "succeeded": succeeded,
            "failed": failed,
            "dead": dead,
            "skipped_due_to_limit": skipped_due_to_limit,
            "errors": errors
        }
    
    def _recover_stuck_jobs(self) -> int:
        """
        Восстановить "залипшие" processing jobs (recovery).
        
        Returns:
            Количество восстановленных jobs
        """
        stuck_jobs = self.email_ocr_job_repo.get_stuck_processing_jobs(
            timeout_seconds=self.processing_timeout_seconds
        )
        
        if not stuck_jobs:
            return 0
        
        recovered_count = 0
        
        for job in stuck_jobs:
            job_id = job["id"]
            tenant_id = job["tenant_id"]
            document_artifact_id = job["document_artifact_id"]
            attempts = job["attempts"]
            max_retries = job.get("max_retries", self.max_retries)
            
            logger.warning(
                f"Recovering stuck job: job_id={job_id}, tenant={tenant_id}, "
                f"document={document_artifact_id}, attempts={attempts}, "
                f"updated_at={job.get('updated_at')}"
            )
            
            new_attempts = attempts + 1
            
            # Проверяем, не превышен ли max_retries
            if new_attempts >= max_retries:
                # Отмечаем job как dead
                self.email_ocr_job_repo.mark_job_dead(
                    job_id=job_id,
                    error_message=f"Processing timeout after {self.processing_timeout_seconds}s (max retries exceeded)"
                )
                
                # Эмитим событие
                try:
                    self.event_service.emit(
                        event_type="email.ocr.dead",
                        tenant_id=tenant_id,
                        artifact_id=document_artifact_id,
                        payload={
                            "job_id": job_id,
                            "reason": "timeout",
                            "attempts": new_attempts
                        }
                    )
                except Exception as e:
                    logger.warning(f"Failed to emit email.ocr.dead event: {e}")
                
                logger.error(
                    f"Stuck job marked as dead: job_id={job_id}, attempts={new_attempts}/{max_retries}"
                )
                
            else:
                # Вычисляем next_run_at с backoff
                next_run_at = self._calculate_next_run_at(new_attempts)
                
                # Отмечаем job как failed и планируем retry
                self.email_ocr_job_repo.mark_job_failed(
                    job_id=job_id,
                    error_message=f"Processing timeout after {self.processing_timeout_seconds}s",
                    next_run_at=next_run_at,
                    attempts=new_attempts
                )
                
                # Эмитим событие о recovery
                try:
                    self.event_service.emit(
                        event_type="email.ocr.recovered",
                        tenant_id=tenant_id,
                        artifact_id=document_artifact_id,
                        payload={
                            "job_id": job_id,
                            "attempts": new_attempts,
                            "reason": "timeout",
                            "next_run_at": next_run_at
                        }
                    )
                except Exception as e:
                    logger.warning(f"Failed to emit email.ocr.recovered event: {e}")
                
                logger.info(
                    f"Stuck job recovered: job_id={job_id}, attempts={new_attempts}, next_run_at={next_run_at}"
                )
            
            recovered_count += 1
        
        return recovered_count
    
    def _process_single_job(
        self,
        job: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Обработать один job.
        
        Returns:
            {
                "status": "succeeded" | "failed" | "dead" | "skipped",
                "invoice_artifact_id": str | None,
                "error": str | None
            }
        """
        job_id = job["id"]
        tenant_id = job["tenant_id"]
        document_artifact_id = job["document_artifact_id"]
        attempts = job["attempts"]
        max_retries = job.get("max_retries", self.max_retries)
        
        # Пытаемся атомарно "забрать" job
        if not self.email_ocr_job_repo.claim_job_for_processing(job_id):
            logger.debug(f"Job {job_id} already claimed or not ready")
            return {"status": "skipped", "invoice_artifact_id": None, "error": None}
        
        # Эмитим событие о начале обработки
        try:
            self.event_service.emit(
                event_type="email.ocr.started",
                tenant_id=tenant_id,
                artifact_id=document_artifact_id,
                payload={
                    "job_id": job_id,
                    "attempts": attempts + 1
                }
            )
        except Exception as e:
            logger.warning(f"Failed to emit email.ocr.started event: {e}")
        
        try:
            # Проверяем, что document artifact существует и принадлежит tenant
            document_artifact = self.artifact_service.get_artifact(document_artifact_id)
            if not document_artifact:
                raise ValueError(f"Document artifact {document_artifact_id} not found")
            
            if document_artifact.get("tenant_id") != tenant_id:
                raise ValueError(
                    f"Document artifact {document_artifact_id} does not belong to tenant {tenant_id}"
                )
            
            if document_artifact.get("kind") != "document":
                raise ValueError(
                    f"Artifact {document_artifact_id} is not a document (kind={document_artifact.get('kind')})"
                )
            
            # Проверка квот перед запуском OCR (для admin dispatch endpoint)
            if self.billing_enforcement:
                try:
                    self.billing_enforcement.enforce(
                        tenant_id=tenant_id,
                        required_metrics={
                            "invoice_extracted": 1.0,
                            "page_processed": 1.0
                        },
                        operation_name="email_auto_ocr_job"
                    )
                except Exception as quota_error:
                    # Если квота превышена, пропускаем job (не блокируем весь dispatch)
                    logger.warning(
                        f"Quota exceeded for tenant {tenant_id}, skipping OCR job {job_id}: {quota_error}"
                    )
                    # Отмечаем job как failed с специальным сообщением
                    next_run_at = datetime.now().isoformat()  # Можно повторить позже
                    self.email_ocr_job_repo.mark_job_failed(
                        job_id=job_id,
                        error_message=f"Quota exceeded: {str(quota_error)}",
                        next_run_at=next_run_at,
                        attempts=attempts + 1
                    )
                    return {
                        "status": "skipped",
                        "invoice_artifact_id": None,
                        "error": f"Quota exceeded: {str(quota_error)}"
                    }
            
            # Запускаем OCR через doc_agent
            from cyberplat.base_agent import AgentContext
            
            file_id = document_artifact.get("data", {}).get("file_id")
            context = AgentContext(
                artifact_id=document_artifact_id,
                file_id=file_id,
                tenant_id=tenant_id
            )
            
            # Логируем начало обработки (structured logging)
            logger.info(
                f"email_ocr_job_started",
                extra={
                    "job_id": job_id,
                    "tenant_id": tenant_id,
                    "document_artifact_id": document_artifact_id,
                    "attempts": attempts + 1
                }
            )
            
            # Запускаем doc_agent (async)
            import asyncio
            start_time = datetime.now()
            
            # Используем asyncio.run (создаёт новый event loop)
            # Если уже есть running loop, это вызовет ошибку, но в нашем случае
            # use case вызывается из синхронного контекста (endpoint)
            result = asyncio.run(self.doc_agent.run(context))
            
            duration_seconds = (datetime.now() - start_time).total_seconds()
            
            # Логируем успешное завершение (structured logging)
            logger.info(
                f"email_ocr_job_completed",
                extra={
                    "job_id": job_id,
                    "tenant_id": tenant_id,
                    "document_artifact_id": document_artifact_id,
                    "invoice_artifact_id": result.get("artifact_id"),
                    "attempts": attempts + 1,
                    "duration_seconds": duration_seconds
                }
            )
            
            if not result.get("success"):
                error_message = result.get("error", "OCR failed")
                raise RuntimeError(error_message)
            
            invoice_artifact_id = result.get("artifact_id")
            
            if not invoice_artifact_id:
                raise RuntimeError("OCR completed but no invoice artifact created")
            
            # Обновляем artifact_states
            # Document: ui_status="extracted"
            self.artifact_state_repo.update_status(
                tenant_id=tenant_id,
                artifact_id=document_artifact_id,
                ui_status="extracted"
            )
            
            # Invoice: создаём state с ui_status="pending", source_artifact_id=document_artifact_id
            self.artifact_state_repo.create_or_update_state(
                tenant_id=tenant_id,
                artifact_id=invoice_artifact_id,
                ui_status="pending",
                source_artifact_id=document_artifact_id
            )
            
            # Отмечаем job как выполненный
            self.email_ocr_job_repo.mark_job_done(
                job_id=job_id,
                invoice_artifact_id=invoice_artifact_id
            )
            
            # Эмитим событие об успешном завершении
            try:
                self.event_service.emit(
                    event_type="email.ocr.completed",
                    tenant_id=tenant_id,
                    artifact_id=invoice_artifact_id,
                    payload={
                        "job_id": job_id,
                        "document_artifact_id": document_artifact_id,
                        "invoice_artifact_id": invoice_artifact_id,
                        "attempts": attempts + 1
                    }
                )
            except Exception as e:
                logger.warning(f"Failed to emit email.ocr.completed event: {e}")
            
            logger.info(
                f"Email OCR job completed: job_id={job_id}, "
                f"document={document_artifact_id}, invoice={invoice_artifact_id}, tenant={tenant_id}"
            )
            
            return {
                "status": "succeeded",
                "invoice_artifact_id": invoice_artifact_id,
                "error": None
            }
            
        except Exception as e:
            error_message = str(e)
            # Ограничиваем длину error_message (без контента)
            error_message_short = error_message[:500] if len(error_message) > 500 else error_message
            
            # Логируем ошибку (structured logging)
            logger.error(
                f"email_ocr_job_failed",
                extra={
                    "job_id": job_id,
                    "tenant_id": tenant_id,
                    "document_artifact_id": document_artifact_id,
                    "attempts": attempts + 1,
                    "error": error_message_short
                },
                exc_info=True
            )
            
            new_attempts = attempts + 1
            
            # Проверяем, не превышен ли max_retries
            if new_attempts >= max_retries:
                # Отмечаем job как dead
                self.email_ocr_job_repo.mark_job_dead(
                    job_id=job_id,
                    error_message=error_message_short
                )
                
                # Эмитим событие о dead job
                try:
                    self.event_service.emit(
                        event_type="email.ocr.failed",
                        tenant_id=tenant_id,
                        artifact_id=document_artifact_id,
                        payload={
                            "job_id": job_id,
                            "attempts": new_attempts,
                            "error": error_message_short,
                            "status": "dead"
                        }
                    )
                except Exception as e:
                    logger.warning(f"Failed to emit email.ocr.failed event: {e}")
                
                logger.error(
                    f"Email OCR job dead after {new_attempts} attempts: job_id={job_id}, error={error_message_short}"
                )
                
                return {
                    "status": "dead",
                    "invoice_artifact_id": None,
                    "error": error_message_short
                }
            
            # Вычисляем next_run_at с exponential backoff + jitter
            next_run_at = self._calculate_next_run_at(new_attempts)
            
            # Отмечаем job как failed и планируем retry
            self.email_ocr_job_repo.mark_job_failed(
                job_id=job_id,
                error_message=error_message_short,
                next_run_at=next_run_at,
                attempts=new_attempts
            )
            
            # Эмитим событие о failed job (retry scheduled)
            try:
                self.event_service.emit(
                    event_type="email.ocr.failed",
                    tenant_id=tenant_id,
                    artifact_id=document_artifact_id,
                    payload={
                        "job_id": job_id,
                        "attempts": new_attempts,
                        "error": error_message_short,
                        "status": "failed",
                        "next_run_at": next_run_at
                    }
                )
            except Exception as e:
                logger.warning(f"Failed to emit email.ocr.failed event: {e}")
            
            logger.warning(
                f"Email OCR job failed (attempt {new_attempts}/{max_retries}): "
                f"job_id={job_id}, error={error_message_short}, next_run_at={next_run_at}"
            )
            
            return {
                "status": "failed",
                "invoice_artifact_id": None,
                "error": error_message_short
            }
    
    def _calculate_next_run_at(self, attempts: int) -> str:
        """
        Вычислить next_run_at с exponential backoff + jitter.
        
        Формула: base_seconds * (2 ^ (attempts - 1)) + jitter
        Ограничено max_seconds.
        
        Args:
            attempts: Номер попытки (1-based)
            
        Returns:
            ISO format timestamp string
        """
        # Exponential backoff: base * 2^(attempts-1)
        backoff_seconds = self.retry_base_seconds * (2 ** (attempts - 1))
        
        # Ограничиваем максимумом
        backoff_seconds = min(backoff_seconds, self.retry_max_seconds)
        
        # Добавляем jitter (±20%)
        jitter_range = backoff_seconds * 0.2
        jitter = random.uniform(-jitter_range, jitter_range)
        backoff_seconds = max(1, backoff_seconds + jitter)  # Минимум 1 секунда
        
        # Вычисляем next_run_at
        next_run_dt = datetime.now() + timedelta(seconds=backoff_seconds)
        
        return next_run_dt.isoformat()
