#!/bin/bash
set -e

echo "Waiting for postgres..."
while ! pg_isready -h ${POSTGRES_HOST:-postgres} -p ${POSTGRES_PORT:-5432} -U ${POSTGRES_USER:-postgres} > /dev/null 2>&1; do
  sleep 1
done
echo "PostgreSQL is ready!"

echo "Running Alembic migrations..."
alembic upgrade head

echo "Starting application..."
exec "$@"