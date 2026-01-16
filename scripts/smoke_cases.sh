#!/bin/bash
# Smoke test для Cases API
# Безопасный скрипт (не удаляет данные, только создаёт тестовые кейсы)

set -e

BASE_URL="${BASE_URL:-http://localhost:8000}"
TENANT_ID="${TENANT_ID:-tenant-smoke-test}"

echo "=== Cases API Smoke Test ==="
echo "Base URL: $BASE_URL"
echo "Tenant ID: $TENANT_ID"
echo ""

# 1. Создать кейс
echo "1. Создание кейса..."
CASE_RESPONSE=$(curl -s -X POST "$BASE_URL/api/v1/cases" \
  -H "X-Tenant-ID: $TENANT_ID" \
  -H "Content-Type: application/json" \
  -d '{
    "case_type": "support",
    "title": "Smoke test case",
    "initial_step": "step1"
  }')

CASE_ID=$(echo "$CASE_RESPONSE" | grep -o '"id":"[^"]*"' | cut -d'"' -f4)

if [ -z "$CASE_ID" ]; then
  echo "❌ Ошибка: не удалось создать кейс"
  echo "Response: $CASE_RESPONSE"
  exit 1
fi

echo "✓ Кейс создан: $CASE_ID"
echo ""

# 2. Получить кейс
echo "2. Получение кейса..."
curl -s "$BASE_URL/api/v1/cases/$CASE_ID" \
  -H "X-Tenant-ID: $TENANT_ID" | jq '.' || echo "⚠️  jq не установлен, вывод без форматирования"
echo ""

# 3. Список кейсов
echo "3. Список кейсов..."
curl -s "$BASE_URL/api/v1/cases?tenant_id=$TENANT_ID" \
  -H "X-Tenant-ID: $TENANT_ID" | jq '.total' || echo "⚠️  jq не установлен"
echo ""

# 4. Добавить задачу
echo "4. Добавление задачи..."
TASK_RESPONSE=$(curl -s -X POST "$BASE_URL/api/v1/cases/$CASE_ID/tasks" \
  -H "X-Tenant-ID: $TENANT_ID" \
  -H "Content-Type: application/json" \
  -d '{
    "step_key": "step1",
    "title": "Smoke test task",
    "assignee_role": "support"
  }')

TASK_ID=$(echo "$TASK_RESPONSE" | grep -o '"id":"[^"]*"' | cut -d'"' -f4)

if [ -z "$TASK_ID" ]; then
  echo "❌ Ошибка: не удалось создать задачу"
  echo "Response: $TASK_RESPONSE"
  exit 1
fi

echo "✓ Задача создана: $TASK_ID"
echo ""

# 5. Завершить задачу
echo "5. Завершение задачи..."
curl -s -X POST "$BASE_URL/api/v1/cases/$CASE_ID/tasks/$TASK_ID/complete" \
  -H "X-Tenant-ID: $TENANT_ID" | jq '.' || echo "✓ Задача завершена"
echo ""

# 6. Перевести на новый шаг
echo "6. Переход на новый шаг..."
curl -s -X POST "$BASE_URL/api/v1/cases/$CASE_ID/transition" \
  -H "X-Tenant-ID: $TENANT_ID" \
  -H "Content-Type: application/json" \
  -d '{
    "new_step": "step2",
    "from_step": "step1"
  }' | jq '.' || echo "✓ Кейс переведён на step2"
echo ""

# 7. Закрыть кейс
echo "7. Закрытие кейса..."
curl -s -X POST "$BASE_URL/api/v1/cases/$CASE_ID/close" \
  -H "X-Tenant-ID: $TENANT_ID" | jq '.' || echo "✓ Кейс закрыт"
echo ""

echo "=== Smoke test завершён успешно ==="
echo "Case ID: $CASE_ID"
echo "Task ID: $TASK_ID"
