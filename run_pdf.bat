@echo off
chcp 65001 >nul
setlocal

REM === Переходим в папку проекта ===
cd /d "%~dp0"

REM === Проверка: перетащили ли файл ===
if "%~1"=="" (
  echo Перетащи PDF/JPG на этот файл run_pdf.bat
  echo Или запусти так:
  echo    run_pdf.bat "C:\path\file.pdf"
  pause
  exit /b 1
)

REM === Активируем виртуальное окружение ===
if exist ".venv\Scripts\activate.bat" (
  call ".venv\Scripts\activate.bat"
) else (
  echo НЕ найдено окружение .venv. Сначала сделай:
  echo    python -m venv .venv
  echo    .venv\Scripts\activate
  echo    pip install -r requirements.txt
  pause
  exit /b 1
)

REM === Создаём папку out если нет ===
if not exist "out" mkdir "out"

REM === Запуск обработки ===
echo Обрабатываю: %~1
python main.py "%~1"

echo.
echo Готово. Смотри результат:
echo    %cd%\out\result.json
echo.
pause
endlocal
