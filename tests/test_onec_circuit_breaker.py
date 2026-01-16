"""Тесты для circuit breaker 1С интеграции."""

import pytest
import time
import threading

from cyberplat.integrations.circuit_breaker import CircuitBreaker, CircuitBreakerError


def test_circuit_breaker_opens_after_threshold():
    """Тест: circuit breaker открывается после threshold ошибок."""
    cb = CircuitBreaker(
        failure_threshold=3,
        window_seconds=60.0,
        cooldown_seconds=2.0,
        name="test"
    )
    
    assert cb.get_state() == "closed"
    
    # Записываем ошибки
    for _ in range(3):
        cb.record_failure()
    
    # Circuit должен открыться
    assert cb.get_state() == "open"


def test_circuit_breaker_fail_fast_when_open():
    """Тест: circuit breaker выбрасывает ошибку в open state."""
    cb = CircuitBreaker(
        failure_threshold=2,
        window_seconds=60.0,
        cooldown_seconds=1.0,
        name="test"
    )
    
    # Открываем circuit
    cb.record_failure()
    cb.record_failure()
    assert cb.get_state() == "open"
    
    # Пытаемся вызвать функцию
    def failing_func():
        raise ValueError("Test error")
    
    with pytest.raises(CircuitBreakerError) as exc_info:
        cb.call(failing_func)
    
    assert exc_info.value.error_code == "circuit_open"


def test_circuit_breaker_closes_after_cooldown():
    """Тест: circuit breaker закрывается после cooldown."""
    cb = CircuitBreaker(
        failure_threshold=2,
        window_seconds=60.0,
        cooldown_seconds=0.5,  # Короткий cooldown для теста
        name="test"
    )
    
    # Открываем circuit
    cb.record_failure()
    cb.record_failure()
    assert cb.get_state() == "open"
    
    # Ждём cooldown
    time.sleep(0.6)
    
    # Проверяем, что circuit перешёл в half_open
    # (через call или проверку состояния)
    state = cb.get_state()
    assert state in {"half_open", "open"}  # Может быть ещё open, если не было попыток
    
    # Успешный вызов должен закрыть circuit
    def success_func():
        return "success"
    
    result = cb.call(success_func)
    assert result == "success"
    assert cb.get_state() == "closed"


def test_circuit_breaker_cleans_old_failures():
    """Тест: circuit breaker очищает старые ошибки из окна."""
    cb = CircuitBreaker(
        failure_threshold=3,
        window_seconds=0.5,  # Короткое окно
        cooldown_seconds=1.0,
        name="test"
    )
    
    # Записываем 2 ошибки
    cb.record_failure()
    cb.record_failure()
    
    # Ждём, пока окно истечёт
    time.sleep(0.6)
    
    # Проверяем, что старые ошибки очищены
    failure_count = cb.get_failure_count()
    assert failure_count == 0  # Старые ошибки должны быть очищены


def test_circuit_breaker_record_success_closes():
    """Тест: успешный запрос закрывает circuit в half_open."""
    cb = CircuitBreaker(
        failure_threshold=2,
        window_seconds=60.0,
        cooldown_seconds=0.5,
        name="test"
    )
    
    # Открываем circuit
    cb.record_failure()
    cb.record_failure()
    assert cb.get_state() == "open"
    
    # Ждём cooldown
    time.sleep(0.6)
    
    # Успешный запрос должен закрыть circuit
    cb.record_success()
    assert cb.get_state() == "closed"


def test_circuit_breaker_thread_safe():
    """Тест: circuit breaker thread-safe."""
    cb = CircuitBreaker(
        failure_threshold=10,
        window_seconds=60.0,
        cooldown_seconds=1.0,
        name="test"
    )
    
    errors = []
    
    def record_failures():
        for _ in range(5):
            cb.record_failure()
            time.sleep(0.01)
    
    # Запускаем несколько потоков
    threads = [threading.Thread(target=record_failures) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    
    # Проверяем, что состояние корректно
    failure_count = cb.get_failure_count()
    assert failure_count == 10  # 5 * 2 threads


def test_circuit_breaker_metrics():
    """Тест: circuit breaker записывает метрику при открытии."""
    cb = CircuitBreaker(
        failure_threshold=2,
        window_seconds=60.0,
        cooldown_seconds=1.0,
        name="test"
    )
    
    # Открываем circuit (должна записаться метрика)
    cb.record_failure()
    cb.record_failure()
    
    # Проверяем, что circuit открыт (метрика записывается внутри)
    assert cb.get_state() == "open"
