FROM python:3.11-slim

WORKDIR /app

# Чтобы некоторые пакеты ставились без сюрпризов
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY . /app

# Создаем необходимые директории
RUN mkdir -p /app/out/temp /app/out/cache /app/out/cache_pages /app/out/jobs

# По умолчанию запускаем FastAPI сервер
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
