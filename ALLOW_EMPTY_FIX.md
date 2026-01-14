# Исправление allow-empty коммитов

## ✅ Что было исправлено

### Проблема
Pre-commit hook блокировал `git commit --allow-empty` с сообщением "Нет файлов для коммита".

### Решение
1. **Обработка пустых staged файлов:**
   - Если `staged_files` пуст → пропустить проверки (exit 0)
   - Нейтральное сообщение: "Нет staged файлов: проверки пропущены (allow-empty commit)"

2. **Надёжное определение staged файлов:**
   - Используется `--diff-filter=ACMR` (Added, Copied, Modified, Renamed)
   - Исключаются удалённые файлы (Deleted)

3. **Подсчёт файлов:**
   - Используется `wc -l` для подсчёта
   - Корректная обработка пустых результатов

## 📋 Изменения

### Pre-commit hook

**До:**
```bash
staged_files=$(git diff --cached --name-only)

if [ -z "$staged_files" ]; then
    echo "${YELLOW}⚠️${NC}  Нет файлов для коммита (используйте git add)"
    exit 1
fi
```

**После:**
```bash
staged_files=$(git diff --cached --name-only --diff-filter=ACMR)

# Обработка allow-empty коммитов: если staged файлов нет, пропускаем проверки
if [ -z "$staged_files" ]; then
    echo "${GREEN}✓${NC} Нет staged файлов: проверки пропущены (allow-empty commit)"
    echo ""
    exit 0
fi
```

### Дополнительные улучшения

- Все проверки используют `--diff-filter=ACMR` для консистентности
- Подсчёт файлов через `wc -l` (надёжнее на Windows)

## 🧪 Тестирование

### Тест 1: allow-empty коммит
```bash
git commit --allow-empty -m "Test: allow-empty"
# ✓ Должен пройти без ошибок
# ✓ Показывает: "Нет staged файлов: проверки пропущены (allow-empty commit)"
```

### Тест 2: Обычный коммит
```bash
echo "test" > test.txt
git add test.txt
git commit -m "Test: обычный коммит"
# ✓ Должен выполнить все проверки
# ✓ Блокирует секреты/БД/большие файлы
```

### Тест 3: Коммит с секретом
```bash
echo "SECRET=123" > test.env
git add test.env
git commit -m "Test: секрет"
# ✓ Должен быть заблокирован (exit 1)
# ✓ Показывает причину блокировки
```

## ✅ Критерии готовности

- ✅ `git commit --allow-empty -m "x"` проходит без ошибок
- ✅ Обычные коммиты проверяются как раньше
- ✅ Безопасность сохранена (секреты блокируются)
- ✅ Надёжная работа на Windows

## 📝 См. также

- `GIT_HOOKS_SECURITY.md` - полная документация по безопасности
- `test-allow-empty.sh` - скрипт для тестирования

---

**Статус:** ✅ Исправлено
**Дата:** 2025-01-14
