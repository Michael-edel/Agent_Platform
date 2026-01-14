# 🔒 Git Hooks - Безопасная настройка

## ✅ Обновления hooks (безопасная версия)

Hooks были обновлены для обеспечения безопасности production-grade SaaS проекта.

---

## 🔍 Pre-commit Hook

### Критические проверки (БЛОКИРУЮТ коммит)

#### ❌ 1. Секреты (exit 1)
**Блокирует коммит при обнаружении:**
- `.env`, `.env.*`
- `*.key`, `*.pem`, `*.p12`, `*.pfx`
- Файлы с `secret`, `password`, `token`, `api_key`, `private_key`, `credential` в имени

**Действия при обнаружении:**
- ❌ Коммит блокируется
- Показывается список файлов с секретами
- Инструкции по исправлению

#### ❌ 2. Файлы баз данных (exit 1)
**Блокирует коммит при обнаружении:**
- `*.db`, `*.sqlite`, `*.sqlite3`
- `*.dump`, `*.sql.dump`, `*.backup`

**Действия при обнаружении:**
- ❌ Коммит блокируется
- Показывается список файлов БД с размерами
- Напоминание добавить в `.gitignore`

#### ❌ 3. Большие файлы > 1MB (exit 1)
**Блокирует коммит при обнаружении:**
- Любые файлы размером > 1MB

**Действия при обнаружении:**
- ❌ Коммит блокируется
- Показывается список файлов с размерами
- Рекомендации использовать Git LFS

#### ✅ 4. Проверка синтаксиса Python (exit 1)
**Проверяет (если есть Python файлы):**
- Синтаксис Python через `py_compile`
- Только staged файлы (быстро)
- Блокирует коммит при ошибках

---

## 🚀 Post-commit Hook

### Условия для автопуша

Автопуш выполняется **ТОЛЬКО если:**

✅ **Все условия выполнены:**
1. `GIT_AUTO_PUSH != 0` (не отключён через env)
2. Не выполняется rebase/merge/cherry-pick
3. Remote origin настроен
4. Есть коммиты для push

### Управление автопушем

#### Отключить временно (для одного коммита):
```bash
GIT_AUTO_PUSH=0 git commit -m "message"
```

#### Отключить глобально (git config):
```bash
git config --global git-auto-push.enabled false
# или для локального репозитория
git config --local git-auto-push.enabled false
```

#### Включить обратно:
```bash
git config --global --unset git-auto-push.enabled
# или
git config --local --unset git-auto-push.enabled
```

#### Через переменную окружения:
```bash
# В PowerShell
$env:GIT_AUTO_PUSH = "0"

# В Bash
export GIT_AUTO_PUSH=0
```

### Автоматически пропускается при:

- ⚠️  **Rebase**: `.git/rebase-merge` или `.git/rebase-apply` существует
- ⚠️  **Merge**: `.git/MERGE_HEAD` существует
- ⚠️  **Cherry-pick**: `.git/CHERRY_PICK_HEAD` существует
- ⚠️  **Env переменная**: `GIT_AUTO_PUSH=0`
- ⚠️  **Git config**: `git-auto-push.enabled = false`

### Вывод статуса

**Успешный push:**
```
✓ PUSH УСПЕШНО ВЫПОЛНЕН
```

**Пропущен (с причиной):**
```
⚠  Автопуш отключён через GIT_AUTO_PUSH=0
   Push пропущен
```

**Ошибка:**
```
✗ ОШИБКА ПРИ PUSH (код: 1)
[Возможные причины и действия]
```

---

## 🧪 Тестирование hooks

### Тест 1: Блокировка секретов
```bash
# Должен заблокировать коммит
echo "SECRET_KEY=123" > test.env
git add test.env
git commit -m "Test: секреты"  # ❌ Должен заблокировать
```

### Тест 2: Блокировка БД файлов
```bash
# Создать тестовый .db файл
touch test.db
git add test.db
git commit -m "Test: БД файл"  # ❌ Должен заблокировать
```

### Тест 3: Блокировка больших файлов
```bash
# Создать файл > 1MB (Linux/Mac)
dd if=/dev/zero of=large.bin bs=1M count=2
git add large.bin
git commit -m "Test: большой файл"  # ❌ Должен заблокировать
```

### Тест 4: Автопуш работает
```bash
# Обычный коммит
echo "test" > test.txt
git add test.txt
git commit -m "Test: автопуш"  # ✓ Должен сделать push
```

### Тест 5: Автопуш отключён
```bash
# С env переменной
GIT_AUTO_PUSH=0 git commit -m "Test: без автопуша"  # ⚠ Push пропущен
```

### Тест 6: Автопуш пропускается при rebase
```bash
# Во время rebase
git rebase -i HEAD~2
# Коммиты во время rebase не должны пушиться автоматически
```

---

## 📋 Чеклист безопасности

✅ **Секреты защищены:**
- `.env` файлы не могут быть закоммичены
- API keys, tokens, passwords блокируются

✅ **БД файлы защищены:**
- `.db`, `.sqlite` файлы не могут быть закоммичены
- Защита от случайного коммита дампов

✅ **Большие файлы защищены:**
- Файлы > 1MB блокируются
- Предотвращение засорения репозитория

✅ **Автопуш безопасен:**
- Не работает во время rebase/merge
- Можно отключить через env/config
- Понятные сообщения о статусе

✅ **Синтаксис Python проверяется:**
- Ошибки компиляции блокируют коммит
- Быстрая проверка только staged файлов

---

## 🔧 Устранение проблем

### Hook не выполняется
```bash
# Проверить права
ls -la .git/hooks/pre-commit
ls -la .git/hooks/post-commit

# Установить права (Linux/Mac/Git Bash)
chmod +x .git/hooks/pre-commit
chmod +x .git/hooks/post-commit
```

### Pre-commit блокирует легитимные файлы

Если файл безопасен, но блокируется (ложное срабатывание):

1. **Переименуйте файл** (уберите ключевые слова из имени)
2. **Добавьте исключение** в pre-commit hook (отредактируйте `.git/hooks/pre-commit`)
3. **Используйте `--no-verify`** (НЕ рекомендуется для production):
   ```bash
   git commit --no-verify -m "message"  # ⚠️ ОПАСНО!
   ```

### Post-commit не делает push

**Проверьте:**
1. `GIT_AUTO_PUSH` не установлен в `0`
2. Не выполняется rebase/merge
3. Remote origin настроен: `git remote -v`
4. Есть коммиты для push: `git log origin/branch..HEAD`

### Python проверка не работает

**На Windows:** Убедитесь, что Python доступен через `python` или `python3`:
```bash
which python  # или
python --version
```

Если Python не найден, проверка синтаксиса будет пропущена (предупреждение).

---

## 📚 См. также

- `GIT_AUTO_COMMIT_README.md` - общая документация по hooks
- `QUICK_START.md` - быстрый старт

---

**Статус:** ✅ Безопасные hooks настроены и активны
**Версия:** 2.0 (безопасная)
**Дата:** 2025-01-14
