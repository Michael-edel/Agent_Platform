# Multi-stage build для оптимизации размера образа
FROM python:3.13-slim as builder

WORKDIR /app

# Установка системных зависимостей для сборки пакетов
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    && rm -rf /var/lib/apt/lists/*

# Копируем requirements и устанавливаем зависимости
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --user -r /app/requirements.txt

# Test stage (dev dependencies + pytest)
FROM python:3.13-slim as test

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/home/appuser/.local/bin:$PATH

RUN groupadd -r appuser && useradd -r -g appuser appuser

# Runtime deps (from builder)
COPY --from=builder /root/.local /home/appuser/.local

# App code + dev deps
COPY . /app
COPY requirements-dev.txt /app/requirements-dev.txt

RUN pip install --no-cache-dir --user -r /app/requirements-dev.txt \
    && chown -R appuser:appuser /app

USER appuser

CMD ["python", "-m", "pytest", "-q"]

# Production stage
FROM python:3.13-slim

WORKDIR /app

# Переменные окружения для Python
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/home/appuser/.local/bin:$PATH

# Создаем non-root пользователя для безопасности
RUN groupadd -r appuser && useradd -r -g appuser appuser

# Копируем установленные пакеты из builder stage
COPY --from=builder /root/.local /home/appuser/.local

# Копируем код приложения
COPY . /app

# Создаем необходимые директории с правильными правами
RUN mkdir -p /app/out/temp /app/out/cache /app/out/cache_pages /app/out/jobs \
    && chown -R appuser:appuser /app

# Переключаемся на non-root пользователя
USER appuser

# Проверка здоровья (healthcheck)
# Используем urllib из стандартной библиотеки Python (не требует дополнительных зависимостей)
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=5)" || exit 1

# По умолчанию запускаем FastAPI сервер
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

COPY requirements-dev.txt /app/requirements-dev.txt
RUN pip install --no-cache-dir --user -r /app/requirements-dev.txt
