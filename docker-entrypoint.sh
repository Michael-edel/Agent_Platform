#!/bin/bash
set -e

echo "Waiting for postgres..."
while ! pg_isready -h ${POSTGRES_HOST:-postgres} -p ${POSTGRES_PORT:-5432} -U ${POSTGRES_USER:-postgres} > /dev/null 2>&1; do
  sleep 1
done
echo "PostgreSQL is ready!"

if [ "${RUN_MIGRATIONS:-true}" = "true" ]; then
  echo "Running Alembic migrations..."
  alembic upgrade head
else
  echo "Skipping Alembic migrations."
fi

echo "Starting application..."
exec "$@"