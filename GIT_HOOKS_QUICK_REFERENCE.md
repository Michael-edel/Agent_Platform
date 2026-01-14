# ⚡ Git Hooks - Быстрая справка

## 🔒 Pre-commit: Что блокируется

❌ **Коммит блокируется (exit 1) при:**

| Что | Паттерн | Пример |
|-----|---------|--------|
| **Секреты** | `.env`, `*.key`, `*.pem`, `secret`, `password`, `token` | `.env`, `api.key`, `secret.txt` |
| **БД файлы** | `*.db`, `*.sqlite`, `*.sqlite3`, `*.dump` | `platform.db`, `backup.dump` |
| **Большие файлы** | > 1MB | `large.bin` (2MB) |
| **Python ошибки** | Синтаксис | `syntax error` |

## 🚀 Post-commit: Автопуш

### ✅ Автопуш выполняется если:
- `GIT_AUTO_PUSH != 0` (не отключён)
- Не rebase/merge/cherry-pick
- Remote origin настроен

### ⚠️ Автопуш пропускается если:
- `GIT_AUTO_PUSH=0` 
- Идёт rebase/merge
- Нет коммитов для push

## 📋 Команды

### Отключить автопуш (временно)
```bash
GIT_AUTO_PUSH=0 git commit -m "message"
```

### Отключить автопуш (навсегда)
```bash
git config --global git-auto-push.enabled false
```

### Включить автопуш обратно
```bash
git config --global --unset git-auto-push.enabled
```

### Обойти pre-commit (⚠️ ОПАСНО!)
```bash
git commit --no-verify -m "message"  # НЕ рекомендуется!
```

## 🧪 Быстрая проверка

```bash
# Проверить работу hooks
bash test-hooks.sh

# Тест блокировки секретов
echo "SECRET=123" > test.env
git add test.env
git commit -m "test"  # Должен заблокировать

# Тест автопуша
echo "test" > test.txt
git add test.txt
git commit -m "test"  # Должен сделать push
```

## ⚠️ Что делать если блокируется?

**Секреты:**
1. Удалить из staging: `git restore --staged <файл>`
2. Добавить в `.gitignore`

**БД файлы:**
1. Удалить из staging: `git restore --staged <файл>`
2. Убедиться, что в `.gitignore` есть `*.db`, `*.sqlite`

**Большие файлы:**
1. Удалить из staging: `git restore --staged <файл>`
2. Использовать Git LFS или добавить в `.gitignore`

**Python ошибки:**
1. Исправить синтаксические ошибки
2. Проверить: `python -m py_compile <файл>`

---

**Версия:** 2.0 (безопасная)
**Полная документация:** `GIT_HOOKS_SECURITY.md`
