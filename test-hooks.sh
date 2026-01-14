#!/bin/bash
#
# Тестовый скрипт для проверки работы git hooks
# Использование: ./test-hooks.sh

echo "🧪 Тестирование git hooks..."
echo ""

# Цвета
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

# Тест 1: Проверка прав на выполнение
echo "Тест 1: Проверка прав на выполнение hooks..."
if [ -x ".git/hooks/pre-commit" ] && [ -x ".git/hooks/post-commit" ]; then
    echo "${GREEN}✓${NC} Hooks имеют права на выполнение"
else
    echo "${RED}✗${NC} Hooks не имеют прав на выполнение"
    echo "  Запустите: chmod +x .git/hooks/pre-commit .git/hooks/post-commit"
fi
echo ""

# Тест 2: Проверка содержимого pre-commit
echo "Тест 2: Проверка pre-commit hook..."
if grep -q "БЛОКИРУЕТ коммит" ".git/hooks/pre-commit" 2>/dev/null; then
    echo "${GREEN}✓${NC} Pre-commit hook содержит блокировку секретов"
else
    echo "${RED}✗${NC} Pre-commit hook не содержит блокировку"
fi
echo ""

# Тест 3: Проверка содержимого post-commit
echo "Тест 3: Проверка post-commit hook..."
if grep -q "GIT_AUTO_PUSH" ".git/hooks/post-commit" 2>/dev/null; then
    echo "${GREEN}✓${NC} Post-commit hook проверяет GIT_AUTO_PUSH"
else
    echo "${RED}✗${NC} Post-commit hook не проверяет GIT_AUTO_PUSH"
fi

if grep -q "rebase\|merge" ".git/hooks/post-commit" 2>/dev/null; then
    echo "${GREEN}✓${NC} Post-commit hook проверяет rebase/merge"
else
    echo "${RED}✗${NC} Post-commit hook не проверяет rebase/merge"
fi
echo ""

# Тест 4: Проверка .gitignore
echo "Тест 4: Проверка .gitignore..."
if grep -q "\.env" ".gitignore" 2>/dev/null && grep -q "\.db" ".gitignore" 2>/dev/null; then
    echo "${GREEN}✓${NC} .gitignore содержит защиту от .env и .db файлов"
else
    echo "${YELLOW}⚠${NC}  .gitignore может не содержать все необходимые паттерны"
fi
echo ""

echo "${GREEN}Готово!${NC}"
echo ""
echo "Для полного тестирования:"
echo "  1. Попробуйте закоммитить .env файл (должен быть заблокирован)"
echo "  2. Попробуйте закоммитить .db файл (должен быть заблокирован)"
echo "  3. Сделайте обычный коммит (должен выполниться push)"
