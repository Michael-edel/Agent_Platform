# Multi-stage build для оптимизации размера образа
FROM python:3.13-slim as builder

WORKDIR /app

# Установка системных зависимостей для сборки пакетов
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# Копируем requirements и устанавливаем зависимости
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --user -r /app/requirements.txt

# Test stage (dev dependencies + pytest)
FROM python:3.13-slim as test

WORKDIR /app

# Установка wget и postgresql-client
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /root/.local /root/.local
ENV PATH=/root/.local/bin:$PATH

COPY requirements-dev.txt /app/requirements-dev.txt
RUN pip install --no-cache-dir --user -r /app/requirements-dev.txt

COPY . /app

CMD ["python", "-m", "pytest", "-q"]

# Production stage
FROM python:3.13-slim

WORKDIR /app

# Переменные окружения для Python
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/home/appuser/.local/bin:$PATH

# Установка postgresql-client для pg_isready
RUN apt-get update && apt-get install -y --no-install-recommends \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# Создаем non-root пользователя для безопасности
RUN groupadd -r appuser && useradd -r -g appuser appuser

# Копируем установленные пакеты из builder stage
COPY --from=builder /root/.local /home/appuser/.local
RUN chown -R appuser:appuser /home/appuser/.local

# Копируем код приложения
COPY . /app

# Копируем entrypoint скрипт
COPY docker-entrypoint.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# Создаем необходимые директории с правильными правами
RUN mkdir -p /app/out/temp /app/out/cache /app/out/cache_pages /app/out/jobs \
    && chown -R appuser:appuser /app

# Проверка здоровья (healthcheck)
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD python -c \"import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=5)\" || exit 1

# Используем entrypoint для запуска миграций (ДО USER appuser)
ENTRYPOINT [\"/usr/local/bin/docker-entrypoint.sh\"]

# Переключаемся на non-root пользователя ПОСЛЕ entrypoint
USER appuser

# По умолчанию запускаем FastAPI сервер
CMD [\"uvicorn\", \"app.main:app\", \"--host\", \"0.0.0.0\", \"--port\", \"8000\"]
