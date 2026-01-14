# Автоматический коммит и push - Настройка

## ⚠️ ВНИМАНИЕ

Автоматический коммит и push настроен! Теперь **все коммиты автоматически пушатся** в remote.

## Что было настроено

### 1. Post-commit hook (`.git/hooks/post-commit`)
Автоматически пушит изменения после каждого коммита.

### 2. Pre-commit hook (`.git/hooks/pre-commit`)
Проверяет изменения перед коммитом:
- Предупреждает о больших файлах (>1MB)
- Предупреждает о возможных секретах (.env, password, key, token)
- Предупреждает о файлах БД (.db, .sqlite)

### 3. Скрипты для ручного запуска
- `git-auto-commit.sh` (Linux/Mac)
- `git-auto-commit.bat` (Windows)

## Как использовать

### Автоматически (через hooks)
Просто делайте обычные коммиты:
```bash
git add .
git commit -m "Ваше сообщение"
# Push выполнится автоматически через post-commit hook
```

### Вручную (через скрипты)

**Linux/Mac:**
```bash
./git-auto-commit.sh "Сообщение коммита"
# или без сообщения (будет сгенерировано автоматически)
./git-auto-commit.sh
```

**Windows:**
```cmd
git-auto-commit.bat "Сообщение коммита"
git-auto-commit.bat
```

## Управление

### Отключить автоматический push

**Linux/Mac/Git Bash:**
```bash
chmod -x .git/hooks/post-commit
```

**Windows (PowerShell):**
```powershell
# Переименовать файл
Rename-Item .git\hooks\post-commit .git\hooks\post-commit.disabled
```

**Или просто переименуйте файл:**
```bash
# Переименовать в post-commit.disabled (hook не будет выполняться)
mv .git/hooks/post-commit .git/hooks/post-commit.disabled
```

### Включить обратно

**Linux/Mac/Git Bash:**
```bash
chmod +x .git/hooks/post-commit
```

**Windows:**
```powershell
# Переименовать обратно
Rename-Item .git\hooks\post-commit.disabled .git\hooks\post-commit

# Или через Git Bash
bash -c "chmod +x .git/hooks/post-commit"
```

### Отключить pre-commit проверки
```bash
chmod -x .git/hooks/pre-commit
```

## Предупреждения безопасности

⚠️ **Важно:**
- Pre-commit hook будет спрашивать подтверждение при коммите файлов с возможными секретами
- Большие файлы (>1MB) будут вызывать предупреждение
- Файлы БД будут вызывать предупреждение

## Проверка работы

После настройки проверьте:
```bash
# Сделайте тестовый коммит
echo "test" > test.txt
git add test.txt
git commit -m "Test: проверка автоматического push"

# Проверьте, что изменения запушены
git log --oneline -1
git status
```

## Откат изменений

Если нужно откатить настройки:
```bash
# Удалить hooks
rm .git/hooks/post-commit
rm .git/hooks/pre-commit

# Восстановить из samples (если нужно)
cp .git/hooks/post-commit.sample .git/hooks/post-commit
cp .git/hooks/pre-commit.sample .git/hooks/pre-commit
```

## Troubleshooting

### Hook не выполняется

**Проверьте права на выполнение:**

Linux/Mac/Git Bash:
```bash
ls -la .git/hooks/post-commit
chmod +x .git/hooks/post-commit
```

Windows (через Git Bash):
```bash
bash -c "chmod +x .git/hooks/post-commit"
```

**Проверьте, что hook выполняется:**
```bash
# Сделайте тестовый коммит
git commit --allow-empty -m "Test: проверка hook"
# Должно появиться сообщение от post-commit hook
```

### Push не работает
Проверьте:
- Подключение к интернету
- Права доступа к remote
- Настройки git config (user.name, user.email)

### Нужна помощь?
Проверьте логи:
```bash
# Смотреть вывод hooks
git commit -m "test"  # Вы увидите вывод hooks

# Проверить настройки remote
git remote -v
```
