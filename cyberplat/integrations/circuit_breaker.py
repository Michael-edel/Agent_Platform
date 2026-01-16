"""Circuit breaker для защиты от каскадных отказов при интеграциях."""

import logging
import time
import threading
from typing import Optional
from collections import deque

logger = logging.getLogger(__name__)


class CircuitBreakerError(Exception):
    """Ошибка при открытом circuit breaker."""
    def __init__(self, message: str, error_code: str = "circuit_open"):
        self.error_code = error_code
        super().__init__(message)


class CircuitBreaker:
    """
    Lightweight circuit breaker для защиты от каскадных отказов.
    
    Открывается при превышении threshold ошибок в окне времени.
    Закрывается после cooldown периода.
    """
    
    def __init__(
        self,
        failure_threshold: int = 10,
        window_seconds: float = 60.0,
        cooldown_seconds: float = 120.0,
        name: str = "circuit"
    ):
        """
        Инициализировать circuit breaker.
        
        Args:
            failure_threshold: Количество ошибок для открытия
            window_seconds: Окно времени для подсчёта ошибок
            cooldown_seconds: Время cooldown перед закрытием
            name: Имя circuit breaker (для логирования)
        """
        self.failure_threshold = failure_threshold
        self.window_seconds = window_seconds
        self.cooldown_seconds = cooldown_seconds
        self.name = name
        
        # Состояние: "closed", "open", "half_open"
        self.state = "closed"
        self._lock = threading.Lock()
        
        # История ошибок (timestamps)
        self._failure_timestamps: deque = deque()
        self._last_failure_time: Optional[float] = None
        self._opened_at: Optional[float] = None
    
    def _clean_old_failures(self, now: float) -> None:
        """Удалить старые ошибки из окна."""
        cutoff = now - self.window_seconds
        while self._failure_timestamps and self._failure_timestamps[0] < cutoff:
            self._failure_timestamps.popleft()
    
    def _should_open(self, now: float) -> bool:
        """Проверить, нужно ли открыть circuit breaker."""
        self._clean_old_failures(now)
        return len(self._failure_timestamps) >= self.failure_threshold
    
    def _should_close(self, now: float) -> bool:
        """Проверить, можно ли закрыть circuit breaker."""
        if self._opened_at is None:
            return False
        return (now - self._opened_at) >= self.cooldown_seconds
    
    def record_success(self) -> None:
        """Записать успешный запрос."""
        with self._lock:
            if self.state == "half_open":
                # Успешный запрос в half_open -> закрываем
                self.state = "closed"
                self._opened_at = None
                logger.info(f"Circuit breaker '{self.name}' closed after successful request")
            elif self.state == "open":
                # Переходим в half_open для тестирования
                self.state = "half_open"
                logger.info(f"Circuit breaker '{self.name}' half-open (testing)")
    
    def record_failure(self) -> None:
        """Записать ошибку."""
        now = time.time()
        with self._lock:
            self._failure_timestamps.append(now)
            self._last_failure_time = now
            
            if self.state == "closed":
                if self._should_open(now):
                    self.state = "open"
                    self._opened_at = now
                    logger.warning(
                        f"Circuit breaker '{self.name}' opened "
                        f"({len(self._failure_timestamps)} failures in {self.window_seconds}s)"
                    )
                    
                    # Метрика
                    try:
                        from cyberplat.observability.metrics import onec_circuit_open_total, METRICS_ENABLED
                        if METRICS_ENABLED and onec_circuit_open_total:
                            onec_circuit_open_total.inc()
                    except Exception:
                        pass
            elif self.state == "half_open":
                # Ошибка в half_open -> снова открываем
                self.state = "open"
                self._opened_at = now
                logger.warning(f"Circuit breaker '{self.name}' reopened after failure in half-open")
    
    def call(self, func, *args, **kwargs):
        """
        Вызвать функцию через circuit breaker.
        
        Args:
            func: Функция для вызова
            *args, **kwargs: Аргументы функции
            
        Returns:
            Результат функции
            
        Raises:
            CircuitBreakerError: Если circuit breaker открыт
        """
        now = time.time()
        
        with self._lock:
            # Проверяем, можно ли закрыть
            if self.state == "open" and self._should_close(now):
                self.state = "half_open"
                logger.info(f"Circuit breaker '{self.name}' half-open (cooldown expired)")
            
            # Если открыт -> fail-fast
            if self.state == "open":
                raise CircuitBreakerError(
                    f"Circuit breaker '{self.name}' is open (fail-fast mode)",
                    error_code="circuit_open"
                )
        
        # Вызываем функцию
        try:
            result = func(*args, **kwargs)
            self.record_success()
            return result
        except Exception as e:
            self.record_failure()
            raise
    
    def get_state(self) -> str:
        """Получить текущее состояние."""
        with self._lock:
            return self.state
    
    def get_failure_count(self) -> int:
        """Получить количество ошибок в текущем окне."""
        now = time.time()
        with self._lock:
            self._clean_old_failures(now)
            return len(self._failure_timestamps)
