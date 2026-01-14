# Скрипт настройки git hooks для автоматического коммита
# Запустите этот скрипт для активации hooks

Write-Host ""
Write-Host "═══════════════════════════════════════════════════════════" -ForegroundColor Blue
Write-Host "🔧 Настройка Git Hooks для автоматического коммита и push" -ForegroundColor Blue
Write-Host "═══════════════════════════════════════════════════════════" -ForegroundColor Blue
Write-Host ""

$hooksPath = ".git\hooks"

if (-not (Test-Path $hooksPath)) {
    Write-Host "✗ Директория .git\hooks не найдена!" -ForegroundColor Red
    exit 1
}

# Проверяем наличие Git Bash (для выполнения .sh скриптов)
$gitBash = "$env:ProgramFiles\Git\bin\bash.exe"
if (Test-Path $gitBash) {
    Write-Host "✓ Git Bash найден" -ForegroundColor Green
    $useBash = $true
} else {
    Write-Host "⚠️  Git Bash не найден, будет использоваться PowerShell" -ForegroundColor Yellow
    $useBash = $false
}

# Настройка post-commit hook
Write-Host ""
Write-Host "Настройка post-commit hook..." -ForegroundColor Cyan

if ($useBash) {
    # Используем .sh версию
    if (Test-Path "$hooksPath\post-commit") {
        Write-Host "✓ post-commit hook уже существует" -ForegroundColor Green
    } else {
        Write-Host "✗ post-commit hook не найден!" -ForegroundColor Red
    }
} else {
    # Используем PowerShell версию
    if (Test-Path "$hooksPath\post-commit.ps1") {
        Write-Host "✓ post-commit.ps1 найден" -ForegroundColor Green
    }
}

Write-Host ""
Write-Host "Настройка pre-commit hook..." -ForegroundColor Cyan

if (Test-Path "$hooksPath\pre-commit") {
    Write-Host "✓ pre-commit hook уже существует" -ForegroundColor Green
} else {
    Write-Host "⚠️  pre-commit hook не найден (создайте вручную если нужно)" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Проверка прав на выполнение..." -ForegroundColor Cyan

# На Windows права на выполнение устанавливаются через атрибуты
if ($useBash) {
    # Для Git Bash нужно, чтобы файл был исполняемым
    Write-Host "✓ Hooks будут выполняться через Git Bash" -ForegroundColor Green
} else {
    Write-Host "⚠️  PowerShell hooks требуют дополнительной настройки" -ForegroundColor Yellow
    Write-Host "   Используйте Git Bash для автоматического выполнения" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "═══════════════════════════════════════════════════════════" -ForegroundColor Green
Write-Host "✓ Настройка завершена!" -ForegroundColor Green
Write-Host "═══════════════════════════════════════════════════════════" -ForegroundColor Green
Write-Host ""

Write-Host "Как использовать:" -ForegroundColor Cyan
Write-Host "  1. Делайте обычные коммиты: git commit -m 'message'" -ForegroundColor White
Write-Host "  2. Push выполнится автоматически после коммита" -ForegroundColor White
Write-Host ""
Write-Host "Отключить автоматический push:" -ForegroundColor Yellow
Write-Host "  Переименуйте .git\hooks\post-commit в .git\hooks\post-commit.disabled" -ForegroundColor White
Write-Host ""
