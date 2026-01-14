@echo off
REM Скрипт для автоматического коммита и push (Windows)
REM 
REM Использование:
REM   git-auto-commit.bat "Сообщение коммита"
REM   git-auto-commit.bat                    REM Сообщение будет сгенерировано автоматически

echo.
echo ================================================================
echo Автоматический коммит и push
echo ================================================================
echo.

REM Проверяем, что мы в git репозитории
git rev-parse --git-dir >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Это не git репозиторий!
    exit /b 1
)

REM Получаем текущую ветку
for /f "tokens=*" %%i in ('git rev-parse --abbrev-ref HEAD') do set BRANCH=%%i
echo [OK] Ветка: %BRANCH%
echo.

REM Проверяем статус
git status --short >nul 2>&1
if errorlevel 1 (
    echo [WARN] Нет изменений для коммита
    exit /b 0
)

echo [OK] Изменения:
git status --short
echo.

REM Генерируем сообщение коммита
if "%1"=="" (
    set COMMIT_MSG=Update: автоматический коммит %DATE% %TIME%
) else (
    set COMMIT_MSG=%1
)

echo [OK] Сообщение коммита: %COMMIT_MSG%
echo.

REM Добавляем все изменения
echo [INFO] git add -A
git add -A

REM Коммитим
echo [INFO] git commit -m "%COMMIT_MSG%"
git commit -m "%COMMIT_MSG%"

if errorlevel 1 (
    echo [ERROR] Ошибка при создании коммита
    exit /b 1
)

echo.
echo [OK] Коммит создан!
echo.

REM Push
echo [INFO] git push origin %BRANCH%
git push origin %BRANCH%

if errorlevel 1 (
    echo [ERROR] Ошибка при push
    exit /b 1
)

echo.
echo [OK] Push успешно выполнен!
echo.
echo ================================================================
echo Готово!
echo ================================================================
echo.
