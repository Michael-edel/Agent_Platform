# 🚀 Быстрый старт: Автоматический коммит и push

## ✅ Настройка завершена!

Автоматический коммит и push настроен. Теперь **каждый коммит автоматически пушится** в remote.

## 📋 Как использовать

### Обычные коммиты (рекомендуется)
```bash
git add .
git commit -m "Ваше сообщение"
# Push выполнится автоматически! ✓
```

### Через скрипт (автоматическое сообщение)
```bash
# Linux/Mac
./git-auto-commit.sh

# Windows
git-auto-commit.bat
```

## ⚠️ Важно знать

1. **Все коммиты автоматически пушатся** - будьте внимательны!
2. **Pre-commit hook проверяет:**
   - Большие файлы (>1MB) - предупреждение
   - Возможные секреты (.env, password, key) - требует подтверждения
   - Файлы БД (.db, .sqlite) - предупреждение

## 🔧 Отключить автоматический push

**Временно:**
```bash
# Переименовать hook
mv .git/hooks/post-commit .git/hooks/post-commit.disabled
```

**Включить обратно:**
```bash
mv .git/hooks/post-commit.disabled .git/hooks/post-commit
```

## 🧪 Проверка работы

```bash
# Создайте тестовый файл
echo "test" > test-auto-commit.txt
git add test-auto-commit.txt
git commit -m "Test: проверка автоматического push"

# Вы должны увидеть:
# 1. Сообщение от pre-commit hook (проверки)
# 2. Коммит создан
# 3. Сообщение от post-commit hook (автоматический push)
# 4. Push выполнен!

# Удалите тестовый файл
rm test-auto-commit.txt
git add test-auto-commit.txt
git commit -m "Remove: тестовый файл"
```

## 📚 Подробная документация

См. `GIT_AUTO_COMMIT_README.md` для полной документации.

---

**Готово к работе!** 🎉
