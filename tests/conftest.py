"""
Pytest конфигурация и плагины для тестов.

Включает простой asyncio plugin для выполнения async тестов без внешних зависимостей.
"""

import asyncio
import inspect
import pytest


def pytest_configure(config):
    """Регистрируем маркер asyncio."""
    config.addinivalue_line(
        "markers",
        "asyncio: mark test as an asyncio coroutine (deselect with '-m \"not asyncio\"')"
    )


def pytest_pyfunc_call(pyfuncitem):
    """Выполняем async тесты через asyncio.run()."""
    # Проверяем, помечен ли тест маркером asyncio
    if not pyfuncitem.get_closest_marker("asyncio"):
        return None  # Не async тест, пропускаем
    
    # Получаем функцию теста
    testfunction = pyfuncitem.obj
    
    # Проверяем, является ли она coroutine function
    if not inspect.iscoroutinefunction(testfunction):
        return None  # Не coroutine, пропускаем
    
    # Выполняем async тест
    # Получаем аргументы для функции
    funcargs = pyfuncitem.funcargs
    
    # Собираем аргументы в правильном порядке
    argnames = pyfuncitem._fixtureinfo.argnames
    args = [funcargs[name] for name in argnames]
    
    # Выполняем coroutine через asyncio.run()
    # Если loop уже запущен, создаём новый
    try:
        loop = asyncio.get_running_loop()
        # Loop уже запущен - это не должно происходить в обычном pytest,
        # но на всякий случай создаём новый loop в отдельном потоке
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(asyncio.run, testfunction(*args))
            future.result()
    except RuntimeError:
        # Loop не запущен - нормальный случай
        asyncio.run(testfunction(*args))
    
    return True  # Указываем, что мы обработали вызов
