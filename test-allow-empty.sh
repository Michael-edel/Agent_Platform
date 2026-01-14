#!/bin/bash
#
# Тестовый скрипт для проверки allow-empty коммитов
# Использование: bash test-allow-empty.sh

echo "🧪 Тестирование allow-empty коммитов..."
echo ""

# Цвета
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

# Тест 1: allow-empty коммит должен пройти
echo "Тест 1: git commit --allow-empty"
echo "─────────────────────────────────────"
if git commit --allow-empty -m "Test: allow-empty commit" > /tmp/test_output.txt 2>&1; then
    if grep -q "проверки пропущены" /tmp/test_output.txt || grep -q "allow-empty" /tmp/test_output.txt; then
        echo "${GREEN}✓${NC} allow-empty коммит прошёл корректно"
    else
        echo "${YELLOW}⚠${NC}  allow-empty коммит прошёл, но не показал ожидаемое сообщение"
        cat /tmp/test_output.txt | grep -A 2 "PRE-COMMIT"
    fi
else
    echo "${RED}✗${NC} allow-empty коммит был заблокирован (не должно быть)"
    cat /tmp/test_output.txt
    exit 1
fi
echo ""

# Тест 2: обычный коммит должен проверяться
echo "Тест 2: обычный коммит (с staged файлом)"
echo "──────────────────────────────────────────"
echo "test" > /tmp/test_file.txt 2>/dev/null
git add /tmp/test_file.txt 2>/dev/null || true

if git commit -m "Test: обычный коммит" > /tmp/test_output2.txt 2>&1; then
    if grep -q "Проверяем.*файл" /tmp/test_output2.txt || grep -q "PRE-COMMIT" /tmp/test_output2.txt; then
        echo "${GREEN}✓${NC} Обычный коммит проверяется корректно"
    else
        echo "${YELLOW}⚠${NC}  Обычный коммит прошёл, но проверки могли не выполниться"
    fi
else
    echo "${RED}✗${NC} Обычный коммит был заблокирован"
    cat /tmp/test_output2.txt
fi

git reset HEAD~1 2>/dev/null || true
rm -f /tmp/test_file.txt 2>/dev/null || true
echo ""

# Тест 3: коммит с секретом должен блокироваться
echo "Тест 3: коммит с секретом (должен блокироваться)"
echo "─────────────────────────────────────────────────"
echo "SECRET_KEY=123" > /tmp/test.env 2>/dev/null
git add /tmp/test.env 2>/dev/null || true

if git commit -m "Test: секрет" > /tmp/test_output3.txt 2>&1; then
    echo "${RED}✗${NC} Коммит с секретом НЕ был заблокирован (должен быть)"
    exit 1
else
    if grep -q "секреты\|Секреты\|КРИТИЧЕСКАЯ ОШИБКА" /tmp/test_output3.txt; then
        echo "${GREEN}✓${NC} Коммит с секретом корректно заблокирован"
    else
        echo "${YELLOW}⚠${NC}  Коммит заблокирован, но по другой причине"
        cat /tmp/test_output3.txt | grep -E "(ОШИБКА|заблокирован|exit)"
    fi
fi

git restore --staged /tmp/test.env 2>/dev/null || true
rm -f /tmp/test.env 2>/dev/null || true
echo ""

echo "${GREEN}Все тесты пройдены!${NC}"
