#!/bin/bash
#
# Скрипт для автоматического коммита и push изменений
# 
# Использование:
#   ./git-auto-commit.sh "Сообщение коммита"
#   ./git-auto-commit.sh                    # Сообщение будет сгенерировано автоматически
#
# ⚠️  ПРЕДУПРЕЖДЕНИЕ: Автоматически коммитит и пушит все изменения!

# Цвета
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo ""
echo "${BLUE}═══════════════════════════════════════════════════════════${NC}"
echo "${BLUE}🤖 Автоматический коммит и push${NC}"
echo "${BLUE}═══════════════════════════════════════════════════════════${NC}"
echo ""

# Проверяем, что мы в git репозитории
if ! git rev-parse --git-dir > /dev/null 2>&1; then
    echo "${RED}✗${NC} Это не git репозиторий!"
    exit 1
fi

# Получаем текущую ветку
branch=$(git rev-parse --abbrev-ref HEAD)
echo "${GREEN}✓${NC} Ветка: ${GREEN}${branch}${NC}"
echo ""

# Проверяем статус
status=$(git status --short)

if [ -z "$status" ]; then
    echo "${YELLOW}⚠️${NC}  Нет изменений для коммита"
    exit 0
fi

echo "${GREEN}✓${NC} Изменения:"
echo "$status" | head -20
if [ $(echo "$status" | wc -l) -gt 20 ]; then
    echo "   ... и ещё $(($(echo "$status" | wc -l) - 20)) файл(ов)"
fi
echo ""

# Генерируем сообщение коммита
if [ -z "$1" ]; then
    # Автоматическое сообщение на основе изменений
    changed_files=$(git diff --name-only --cached 2>/dev/null || git diff --name-only)
    
    if echo "$changed_files" | grep -q "cyberplat/billing"; then
        commit_message="Refactor: обновление billing системы"
    elif echo "$changed_files" | grep -q "test"; then
        commit_message="Test: обновление тестов"
    elif echo "$changed_files" | grep -q "requirements\|\.py$"; then
        commit_message="Update: обновление кода"
    else
        commit_message="Update: автоматический коммит $(date '+%Y-%m-%d %H:%M:%S')"
    fi
else
    commit_message="$1"
fi

echo "${GREEN}✓${NC} Сообщение коммита: ${GREEN}${commit_message}${NC}"
echo ""

# Добавляем все изменения
echo "${BLUE}→${NC}  git add -A"
git add -A

# Коммитим
echo "${BLUE}→${NC}  git commit -m \"${commit_message}\""
git commit -m "$commit_message"

if [ $? -ne 0 ]; then
    echo "${RED}✗${NC} Ошибка при создании коммита"
    exit 1
fi

echo ""
echo "${GREEN}✓${NC} Коммит создан!"
echo ""

# Push (post-commit hook сделает это автоматически, но можно и здесь)
if git rev-parse --verify origin/${branch} > /dev/null 2>&1; then
    ahead=$(git rev-list --count origin/${branch}..HEAD 2>/dev/null || echo "0")
    if [ "$ahead" -gt "0" ]; then
        echo "${BLUE}→${NC}  git push origin ${branch}"
        git push origin "${branch}"
        
        if [ $? -eq 0 ]; then
            echo ""
            echo "${GREEN}✓${NC} Push успешно выполнен!"
        else
            echo ""
            echo "${RED}✗${NC} Ошибка при push"
            exit 1
        fi
    fi
else
    echo "${BLUE}→${NC}  git push -u origin ${branch}"
    git push -u origin "${branch}"
fi

echo ""
echo "${GREEN}═══════════════════════════════════════════════════════════${NC}"
echo "${GREEN}✓${NC} Готово!"
echo "${GREEN}═══════════════════════════════════════════════════════════${NC}"
echo ""
