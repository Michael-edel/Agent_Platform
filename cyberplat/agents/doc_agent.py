"""Агент для обработки документов через OCR пайплайн."""

import json
import logging
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, Any, Optional

from cyberplat.base_agent import BaseAgent, AgentContext
from cyberplat.artifact_service import ArtifactService
from cyberplat.event_service import EventService, TenantValidationError
from cyberplat.storage_service import StorageService

logger = logging.getLogger(__name__)

# Определяем корень репозитория и путь к main.py
# doc_agent.py находится в cyberplat/agents/, поэтому parents[2] = корень репозитория
_repo_root = Path(__file__).resolve().parents[2]
_main_py = _repo_root / "main.py"


class DocAgent(BaseAgent):
    """Агент для обработки документов."""
    
    def __init__(
        self,
        artifact_service: ArtifactService,
        event_service: EventService,
        storage_service: StorageService
    ):
        super().__init__(
            name="doc_agent",
            description="Агент для распознавания документов через OCR пайплайн"
        )
        self.artifact_service = artifact_service
        self.event_service = event_service
        self.storage_service = storage_service
    
    def _detect_country_hint(self, invoice_data: Dict[str, Any]) -> str:
        """
        Определить country_hint из данных invoice (KZ-first стратегия).
        
        Правила:
        1) Если IBAN начинается с "KZ" → KZ
        2) Если bank_bik содержит буквы (A-Z) → KZ (SWIFT/BIC)
        3) Если bank_bik = ровно 9 цифр → RU
        4) Если address/company_name содержит "Казахстан" → KZ
        5) Иначе → KZ (default)
        
        Args:
            invoice_data: Данные invoice (может быть страница или весь документ)
            
        Returns:
            "KZ" или "RU"
        """
        # Получаем первую страницу, если есть pages
        page_data = invoice_data
        if "pages" in invoice_data and isinstance(invoice_data["pages"], list) and len(invoice_data["pages"]) > 0:
            page_data = invoice_data["pages"][0]
        
        # Извлекаем реквизиты
        payment_data = page_data.get("payment", {})
        account_iban = payment_data.get("beneficiary_account_iban")
        bank_bik = payment_data.get("beneficiary_bank_bik")
        
        # 1) Проверяем IBAN на KZ
        if account_iban:
            account_clean = account_iban.strip().upper()
            if account_clean.startswith("KZ"):
                return "KZ"
        
        # 2) Проверяем BIK на наличие букв (SWIFT/BIC для KZ)
        if bank_bik:
            bik_clean = bank_bik.strip().upper()
            # Если содержит буквы - это SWIFT/BIC (KZ)
            if re.search(r'[A-Z]', bik_clean):
                return "KZ"
            
            # 3) Проверяем на чисто цифровой БИК РФ (9 цифр)
            bik_digits = re.sub(r'\D', '', bik_clean)
            if len(bik_digits) == 9 and bik_digits.isdigit():
                return "RU"
        
        # 4) Проверяем address/company_name на упоминание Казахстана
        supplier = page_data.get("supplier", {})
        company_name = page_data.get("company_name") or supplier.get("name", "")
        address = supplier.get("address", "")
        
        search_text = f"{company_name} {address}".upper()
        if "КАЗАХСТАН" in search_text or "РЕСПУБЛИКА КАЗАХСТАН" in search_text:
            return "KZ"
        
        # 5) Default: KZ (KZ-first стратегия)
        return "KZ"
    
    async def run(self, context: AgentContext) -> Dict[str, Any]:
        """
        Запустить обработку документа.
        
        Args:
            context: Контекст выполнения агента
            
        Returns:
            Результат выполнения
        """
        logger.info(f"Запуск doc_agent для artifact_id={context.artifact_id}, file_id={context.file_id}")
        
        # Получаем tenant_id из исходного артефакта, если не передан в контексте
        tenant_id = context.tenant_id
        if not tenant_id:
            source_artifact = self.artifact_service.get_artifact(context.artifact_id)
            if source_artifact:
                tenant_id = source_artifact.get("tenant_id")
                logger.info(f"tenant_id получен из исходного артефакта: {tenant_id}")
        
        # КРИТИЧНО: tenant_id обязателен для создания invoice артефакта
        # Не создаем артефакт без tenant_id (нарушение tenant isolation)
        if not tenant_id:
            raise TenantValidationError(
                f"tenant_id обязателен для doc_agent. "
                f"Передайте X-Tenant-ID заголовок или убедитесь, что исходный артефакт имеет tenant_id."
            )
        
        # Нормализуем tenant_id
        tenant_id = tenant_id.strip()
        if not tenant_id or tenant_id == "string":
            raise TenantValidationError(
                f"Некорректный tenant_id: '{tenant_id}'. tenant_id не может быть пустым или 'string'"
            )
        
        # Получаем путь к файлу
        if not context.file_id:
            raise ValueError("file_id не указан в контексте")
        
        file_path = self.storage_service.get_file_path(context.file_id)
        if not file_path or not file_path.exists():
            raise FileNotFoundError(f"Файл не найден: {context.file_id}")
        
        logger.info(f"Обработка файла: {file_path}")
        
        # Создаем временную директорию для результата
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_dir_path = Path(temp_dir)
            
            # Запускаем OCR пайплайн
            try:
                # Запускаем main.py как внешний процесс
                # Используем sys.executable для использования Python из venv
                # Передаем директорию - save_json автоматически создаст result.json внутри
                cmd = [
                    sys.executable,      # Python из venv (не системный)
                    str(_main_py),       # абсолютный путь к main.py
                    str(file_path),      # путь к PDF
                    "--out",
                    str(temp_dir_path)   # директория для result.json
                ]
                
                logger.info(f"Запуск команды: {' '.join(cmd)}")
                logger.info(f"Рабочая директория: {_repo_root}")
                
                # Устанавливаем env переменные для защиты от проблем с кодировкой на Windows
                env = dict(os.environ)
                env["PYTHONUTF8"] = "1"
                env["PYTHONIOENCODING"] = "utf-8"
                
                proc = subprocess.run(
                    cmd,
                    cwd=str(_repo_root),  # важно: чтобы main.py видел utils/, core/, etc
                    capture_output=True,
                    text=True,
                    env=env
                )
                
                if proc.returncode != 0:
                    error_msg = (
                        f"OCR failed (code={proc.returncode})\n"
                        f"STDOUT:\n{proc.stdout}\n"
                        f"STDERR:\n{proc.stderr}"
                    )
                    logger.error(error_msg)
                    raise RuntimeError(error_msg)
                
                # Результат сохраняется в temp_dir/result.json
                result_json_path = temp_dir_path / "result.json"
                if not result_json_path.exists():
                    raise FileNotFoundError(f"Результат не создан: {result_json_path}")
                
                with open(result_json_path, "r", encoding="utf-8") as f:
                    result_data = json.load(f)
                
                # Извлекаем данные из результата
                # main.py для одного файла возвращает результат напрямую (Dict)
                # main.py для папки возвращает {"source_file": ..., "error": ..., "result": ...}
                invoice_data = None
                
                if isinstance(result_data, dict):
                    # Проверяем, обернут ли результат в payload
                    if "result" in result_data:
                        # Формат с оберткой (из _process_single_file)
                        if result_data.get("error"):
                            raise RuntimeError(f"Ошибка обработки документа: {result_data['error']}")
                        invoice_data = result_data.get("result")
                    else:
                        # Прямой результат (для одного файла)
                        invoice_data = result_data
                
                if not invoice_data:
                    raise ValueError("Результат обработки пуст")
                
                # Определяем country_hint
                country_hint = self._detect_country_hint(invoice_data)
                logger.info(f"Определен country_hint: {country_hint}")
                
                # Добавляем country_hint в invoice_data
                # Если invoice_data содержит pages, добавляем на верхний уровень и в первую страницу
                if "pages" in invoice_data and isinstance(invoice_data["pages"], list) and len(invoice_data["pages"]) > 0:
                    invoice_data["country_hint"] = country_hint
                    # Также добавляем в первую страницу
                    if len(invoice_data["pages"]) > 0:
                        invoice_data["pages"][0]["country_hint"] = country_hint
                else:
                    # Прямой результат (один файл)
                    invoice_data["country_hint"] = country_hint
                
                # Создаем артефакт
                artifact_id = self.artifact_service.create_artifact(
                    kind="invoice",
                    source="doc_agent",
                    data=invoice_data,
                    tenant_id=tenant_id
                )
                
                # Эмитим событие (tenant_id будет получен из артефакта автоматически)
                # Передаем artifact_id, чтобы EventService сам получил tenant_id из артефакта
                # Это гарантирует консистентность
                self.event_service.emit(
                    event_type="document.extracted",
                    tenant_id=tenant_id,  # Передаем для проверки консистентности
                    artifact_id=artifact_id,
                    payload={
                        "source_artifact_id": context.artifact_id,
                        "country_hint": country_hint
                    }
                )
                
                logger.info(f"Обработка завершена успешно. Создан артефакт: {artifact_id}")
                
                return {
                    "success": True,
                    "artifact_id": artifact_id,
                    "tenant_id": tenant_id
                }
                
            except Exception as e:
                logger.error(f"Ошибка при обработке документа: {e}", exc_info=True)
                raise
