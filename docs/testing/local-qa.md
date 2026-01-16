# Локальный QA (Windows / PowerShell 7)

## Зачем

Один стандартный ритуал для локальной проверки перед ручным тестированием/демо:
- поднять `docker compose`
- прогнать `pytest`
- прогнать `ruff`
- проверить `/health` и `/metrics`
- (опционально) сделать smoke upload PDF

## Запуск

Из корня репозитория:

```powershell
pwsh -File .\scripts\local-qa.ps1
```

## PASS / FAIL

**PASS**, если скрипт завершился с кодом `0` и печатает:
- `PASS: Локальный QA успешно завершён.`

**FAIL**, если скрипт завершился с кодом `1` и печатает строку вида:
- `FAIL: <причина>`

## Где смотреть логи

- `docker compose ps`
- `docker compose logs -f app`
- `docker compose logs -f worker`
- `docker compose logs -f postgres`

## Примечания

- Скрипт **не удаляет данные** и не выполняет destructive-команды.
- Контейнеры остаются запущенными после выполнения (это удобно для дальнейшей ручной проверки).

