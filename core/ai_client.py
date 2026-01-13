"""Клиент для работы с OpenAI API."""

import base64
import json
import logging
import threading
import time
import random
from typing import Optional

from openai import OpenAI, AsyncOpenAI
from openai import RateLimitError

from models.document import DocumentData

logger = logging.getLogger(__name__)

class AIClient:
    def __init__(
        self, 
        api_key: str, 
        model: str = "gpt-4o-mini", 
        max_concurrency: int = 2,
        min_interval_sec: float = 0.7,
        max_retries: int = 5
    ):
        if not api_key:
            raise ValueError("OPENAI_API_KEY не указан")

        api_key = self._clean_api_key(api_key)
        self._validate_api_key(api_key)

        self.api_key = api_key
        self.model = model
        self.sync_client = OpenAI(api_key=api_key)
        self.async_client = AsyncOpenAI(api_key=api_key)
        # Semaphore для ограничения одновременных запросов к OpenAI
        self._semaphore = threading.Semaphore(max_concurrency)
        # Счетчик запросов для статистики
        self._request_count = 0
        self._request_count_lock = threading.Lock()
        # Счетчики токенов
        self._tokens_in = 0
        self._tokens_out = 0
        self._tokens_lock = threading.Lock()
        # Троттлинг: глобальный lock и время последнего запроса
        self._throttle_lock = threading.Lock()
        self._last_request_time = 0.0
        self._min_interval_sec = min_interval_sec
        self._max_retries = max_retries

        logger.info("AIClient инициализирован (model=%s, key=%s..., max_concurrency=%d, min_interval=%s, max_retries=%d)", 
                   model, api_key[:10], max_concurrency, min_interval_sec, max_retries)

    @staticmethod
    def _clean_api_key(api_key: str) -> str:
        cleaned = (api_key or "").strip().replace("\n", "").replace("\r", "")
        if cleaned.startswith('"') and cleaned.endswith('"'):
            cleaned = cleaned[1:-1]
        if cleaned.startswith("'") and cleaned.endswith("'"):
            cleaned = cleaned[1:-1]
        return cleaned

    @staticmethod
    def _validate_api_key(api_key: str) -> None:
        if not api_key.startswith(("sk-", "sk-proj-")):
            raise ValueError("Неверный формат API ключа (ожидается 'sk-' или 'sk-proj-').")
        try:
            api_key.encode("ascii")
        except UnicodeEncodeError as e:
            raise ValueError("API ключ содержит недопустимые символы (не ASCII).") from e

    @staticmethod
    def encode_image(image_bytes: bytes) -> str:
        return base64.b64encode(image_bytes).decode("utf-8")

    def get_document_prompt(self) -> str:
        return """Ты — точный парсер бухгалтерских документов Казахстана. Твоя задача — извлечь данные БЕЗ выдумок и без изменения чисел.
Если чего-то нет в документе — ставь null или пустую строку.

Верни ОДИН JSON-объект со следующими ключами:

БАЗОВЫЕ ПОЛЯ:
1) document_type: строка (например: "счет на оплату", "накладная", "договор", "паспорт", "unknown")
2) iin_bin: строка из 12 цифр (ИИН/БИН компании-продавца/поставщика; если есть)
3) company_name: наименование ПРОДАВЦА/ПОСТАВЩИКА (кто выставил счет)
4) document_number: номер документа (например "ЦБ-11777")
5) document_date: дата документа (любой читаемый формат)
6) subtotal: сумма без НДС (если есть) иначе пусто
7) vat: сумма НДС (если есть) иначе пусто
8) total: итоговая сумма к оплате
9) items: массив позиций. Каждая позиция:
   - name
   - sku (если есть артикул/код)
   - quantity
   - unit
   - price
   - total

ДОПОЛНИТЕЛЬНО ДЛЯ СЧЕТА НА ОПЛАТУ (ОБЯЗАТЕЛЬНО ПЫТАЙСЯ НАЙТИ):
10) supplier: объект (поставщик) с полями: name, bin_iin, address, phone
11) buyer: объект (покупатель) с полями: name, bin_iin, address, phone
12) payment: объект реквизитов платежа:
    - beneficiary_bank_name (банк получателя)
    - beneficiary_bank_bik (БИК)
    - beneficiary_account_iban (счет/IBAN)
    - beneficiary_bin_iin (ИИН/БИН получателя)
    - kbe (КБе)
    - knp (КНП / код назначения платежа / "Код наз. пл." — если есть)
    - payment_code (код/уникальный код платежа, если указан)
    - payment_purpose (назначение платежа)
13) barcodes: массив строк (EAN-13/Code128/QR и т.п.), если присутствуют на документе или в строках товара.

КРИТИЧЕСКИЕ ПРАВИЛА:
- НИКОГДА не пересчитывай суммы и не "исправляй" totals/subtotal/vat. Верни как в документе.
- НИКОГДА не выдумывай реквизиты: если не уверен — null/"".
- company_name = поставщик (кто выставил документ), НЕ покупатель.
- Для document_number старайся вернуть именно номер счета из шапки (часто "Счет на оплату № ...").

Верни строго JSON, без пояснений и без текста вокруг.
"""

    async def analyze_document_async(self, image_bytes: bytes) -> DocumentData:
        try:
            base64_image = self.encode_image(image_bytes)
            response = await self.async_client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": self.get_document_prompt() + ("\n\nТекст страницы (для надежности):\n" + extra_text if extra_text else "")},
                            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}},
                        ],
                    }
                ],
                response_format={"type": "json_object"},
            )
            result_json = response.choices[0].message.content
            result_dict = json.loads(result_json)
            return DocumentData.from_dict(result_dict)
        except json.JSONDecodeError as e:
            logger.error("Ошибка декодирования JSON: %s", e)
            return DocumentData(document_type="unknown", error=f"Ошибка декодирования JSON: {e}")
        except Exception as e:
            logger.error("Ошибка при анализе документа: %s", e, exc_info=True)
            return DocumentData(document_type="unknown", error=self._format_error_message(e))

    def _throttle_request(self) -> None:
        """Троттлинг: обеспечить минимальный интервал между запросами."""
        with self._throttle_lock:
            current_time = time.time()
            time_since_last = current_time - self._last_request_time
            
            if time_since_last < self._min_interval_sec:
                sleep_time = self._min_interval_sec - time_since_last
                logger.debug(f"Throttle sleep {sleep_time:.3f}s")
                time.sleep(sleep_time)
            
            self._last_request_time = time.time()

    def _extract_retry_after(self, error: Exception) -> Optional[float]:
        """Извлечь retry_after из ошибки OpenAI."""
        error_str = str(error).lower()
        # Ищем паттерны типа "retry after 60" или "retry_after: 60"
        import re
        patterns = [
            r"retry[_\s]after[:\s]+(\d+)",
            r"retry[_\s]in[:\s]+(\d+)",
            r"wait[:\s]+(\d+)",
        ]
        for pattern in patterns:
            match = re.search(pattern, error_str)
            if match:
                return float(match.group(1))
        return None

    def _calculate_backoff(self, attempt: int, retry_after: Optional[float] = None) -> float:
        """Рассчитать время задержки для retry (экспоненциальный backoff + jitter)."""
        if retry_after is not None:
            # Используем retry_after из ошибки, добавляем небольшой jitter
            return retry_after + random.uniform(0.1, 0.3)
        
        # Экспоненциальный backoff: base * 2^attempt, с ограничением max
        base = 0.8
        max_delay = 8.0
        delay = min(base * (2 ** attempt), max_delay)
        # Добавляем jitter (±20%)
        jitter = delay * 0.2 * (random.random() * 2 - 1)
        return delay + jitter

    def analyze_document_sync(self, image_bytes: bytes, extra_text: str | None = None) -> DocumentData:
        """Обработать документ с троттлингом и retry при 429 ошибках."""
        # Ограничение одновременных запросов к OpenAI
        with self._semaphore:
            base64_image = self.encode_image(image_bytes)
            messages = [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": self.get_document_prompt() + ("\n\nТекст страницы (для надежности):\n" + extra_text if extra_text else "")},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}},
                    ],
                }
            ]
            
            last_error = None
            for attempt in range(self._max_retries):
                try:
                    # Троттлинг перед запросом
                    self._throttle_request()
                    
                    # Выполняем запрос
                    response = self.sync_client.chat.completions.create(
                        model=self.model,
                        messages=messages,
                        response_format={"type": "json_object"},
                    )
                    
                    # Увеличиваем счетчик запросов и токенов
                    with self._request_count_lock:
                        self._request_count += 1
                    
                    # Собираем статистику по токенам
                    usage = response.usage
                    if usage:
                        with self._tokens_lock:
                            self._tokens_in += (usage.prompt_tokens or 0)
                            self._tokens_out += (usage.completion_tokens or 0)
                    
                    result_json = response.choices[0].message.content
                    result_dict = json.loads(result_json)
                    return DocumentData.from_dict(result_dict)
                    
                except RateLimitError as e:
                    last_error = e
                    retry_after = self._extract_retry_after(e)
                    backoff = self._calculate_backoff(attempt, retry_after)
                    
                    if attempt < self._max_retries - 1:
                        logger.warning(
                            f"Rate limit error (429), retry {attempt + 1}/{self._max_retries} after {backoff:.2f}s"
                        )
                        time.sleep(backoff)
                    else:
                        logger.error(f"Rate limit error (429) after {self._max_retries} attempts: {e}")
                        return DocumentData(
                            document_type="unknown",
                            error=f"RATE_LIMIT: Превышен лимит запросов к OpenAI после {self._max_retries} попыток"
                        )
                    
                except Exception as e:
                    logger.error("Ошибка при синхронном анализе документа: %s", e, exc_info=True)
                    return DocumentData(document_type="unknown", error=self._format_error_message(e))
            
            # Если дошли сюда, значит все retry исчерпаны
            if last_error:
                return DocumentData(
                    document_type="unknown",
                    error=f"RATE_LIMIT: Превышен лимит запросов к OpenAI после {self._max_retries} попыток"
                )
            
            return DocumentData(document_type="unknown", error="Неизвестная ошибка при обработке документа")

    def get_request_count(self) -> int:
        """Получить количество выполненных запросов к OpenAI."""
        with self._request_count_lock:
            return self._request_count

    def get_tokens(self) -> tuple[int, int]:
        """Получить количество использованных токенов (in, out)."""
        with self._tokens_lock:
            return (self._tokens_in, self._tokens_out)

    def reset_counters(self) -> None:
        """Сбросить счетчики запросов и токенов."""
        with self._request_count_lock:
            self._request_count = 0
        with self._tokens_lock:
            self._tokens_in = 0
            self._tokens_out = 0

    @staticmethod
    def _format_error_message(error: Exception) -> str:
        s = str(error)
        if "401" in s or "Unauthorized" in s or "invalid_api_key" in s:
            return "Ошибка авторизации OpenAI API (401). Проверьте ключ и баланс."
        if "codec" in s.lower() or "encode" in s.lower():
            return "Ошибка кодировки. Проверьте API ключ."
        return f"Ошибка при анализе документа: {s}"
