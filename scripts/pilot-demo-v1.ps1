<#
Pilot Demo v1 — один скрипт для цепочки "бухгалтер → директор".

Логика:
- проверяет /health
- запускает scripts/manual-accountant-flow.ps1 и сохраняет stdout в logs/pilot_demo_*.log
- извлекает PaymentId из строки "PaymentId: <...>"
- запускает scripts/manual-approver-flow.ps1 (approve/reject)
- печатает итог (что прошло/что пропущено + PaymentId + финальный статус если удалось распознать)

Без UI, без новых зависимостей, без секретов.
#>

[CmdletBinding()]
Param(
    [Parameter(Mandatory = $false)]
    [string]$PdfPath,

    [Parameter(Mandatory = $false)]
    [string]$TenantId = "demo-tenant",

    [Parameter(Mandatory = $false)]
    [string]$AuthToken = "",

    [Parameter(Mandatory = $false)]
    [ValidateSet("approve","reject")]
    [string]$DirectorAction = "approve",

    [Parameter(Mandatory = $false)]
    [string]$Reason = "Отклонено для теста"
)

$ErrorActionPreference = "Stop"

. (Join-Path $PSScriptRoot "_demo-common.ps1")

try {
    $utf8 = [System.Text.UTF8Encoding]::new($false)
    [Console]::OutputEncoding = $utf8
    $OutputEncoding = $utf8
} catch {
    # best-effort
}

function Write-Step([string]$Text) { Write-Host ""; Write-Host ("==> " + $Text) }
function Write-Ok([string]$Text) { Write-Host ("OK: " + $Text) }
function Write-Warn([string]$Text) { Write-Host ("ПРЕДУПРЕЖДЕНИЕ: " + $Text) }
function Fail([string]$Text) { Write-Host ""; Write-Host ("FAIL: " + $Text); exit 1 }

function Get-HttpStatus([string]$Url) {
    $code = & curl.exe -s -o NUL -w "%{http_code}" $Url
    return [int]$code
}

function Now-Stamp() {
    return (Get-Date).ToString("yyyyMMdd_HHmmss")
}

function Try-Parse([string]$Text, [string]$Regex) {
    $m = [regex]::Match($Text, $Regex, [System.Text.RegularExpressions.RegexOptions]::Multiline)
    if ($m.Success) { return $m.Groups[1].Value }
    return $null
}

$stepsOk = New-Object System.Collections.Generic.List[string]
$stepsSkipped = New-Object System.Collections.Generic.List[string]

$paymentId = $null
$finalStatus = $null
$accountantDone = $null
$accountantSkipped = $null
$approverStatus = $null

try {
    $repoRoot = Get-RepoRoot
    if (-not $PdfPath) {
        $PdfPath = Get-DefaultDemoPdfPath
    }
    if (-not $TenantId) { $TenantId = Get-DefaultTenantId }

    if (-not (Test-Path $PdfPath)) {
        Fail("PDF файл не найден: $PdfPath. Укажите -PdfPath или сгенерируйте demo PDF через scripts/gen-demo-invoice-pdf.py")
    }

    Write-Step "Проверка доступности сервиса (/health)"
    $healthCode = Get-HttpStatus "http://localhost:8000/health"
    if ($healthCode -lt 200 -or $healthCode -ge 300) { Fail("/health вернул HTTP $healthCode") }
    Write-Ok("/health (HTTP $healthCode)")
    $stepsOk.Add("/health") | Out-Null

    $repoRoot = Get-RepoRoot
    $logsDir = Join-Path $repoRoot "logs"
    if (-not (Test-Path $logsDir)) { New-Item -ItemType Directory -Path $logsDir | Out-Null }
    $logFile = Join-Path $logsDir ("pilot_demo_" + (Now-Stamp) + ".log")

    Write-Step "Запуск бухгалтерского прогона (manual-accountant-flow.ps1)"
    $accountantScript = Join-Path $PSScriptRoot "manual-accountant-flow.ps1"
    if (-not (Test-Path $accountantScript)) { Fail("Не найден скрипт: $accountantScript") }

    $accountantArgs = @("-NoProfile","-ExecutionPolicy","Bypass","-File",$accountantScript,"-PdfPath",$PdfPath,"-TenantId",$TenantId)
    if ($AuthToken) { $accountantArgs += @("-AuthToken",$AuthToken) }
    $accountantOut = & pwsh @accountantArgs 2>&1 | Out-String
    $accountantExit = $LASTEXITCODE
    $accountantOut | Set-Content -Path $logFile -Encoding UTF8
    Write-Ok("Лог сохранён: $logFile")

    if ($accountantExit -ne 0) {
        Fail("Бухгалтерский прогон завершился с ошибкой (exit code=$accountantExit). См. лог: $logFile")
    }
    $stepsOk.Add("accountant") | Out-Null

    # Попытаться распарсить сводку бухгалтера (best-effort)
    $accountantDone = Try-Parse $accountantOut "^\- Выполнено:\s*(.+)$"
    $accountantSkipped = Try-Parse $accountantOut "^\- Пропущено:\s*(.+)$"

    Write-Step "Извлечение PaymentId из вывода бухгалтера"
    $paymentId = Try-Parse $accountantOut "PaymentId:\s*(\S+)"
    if (-not $paymentId) {
        $noPaymentReason = Try-Parse $accountantOut "^(PaymentId не получен:.*endpoint недоступен.*)$"
        if ($noPaymentReason) { Write-Warn($noPaymentReason) }
    } else {
        Write-Ok("PaymentId найден: $paymentId")
        $stepsOk.Add("extract PaymentId") | Out-Null
    }

    if ($paymentId) {
        Write-Step "Запуск согласующего (manual-approver-flow.ps1) — действие директора: $DirectorAction"
        $approverScript = Join-Path $PSScriptRoot "manual-approver-flow.ps1"
        if (-not (Test-Path $approverScript)) { Fail("Не найден скрипт: $approverScript") }

        $approverArgs = @("-NoProfile","-ExecutionPolicy","Bypass","-File",$approverScript,"-PaymentId",$paymentId,"-TenantId",$TenantId,"-Action",$DirectorAction,"-Reason",$Reason)
        if ($AuthToken) { $approverArgs += @("-AuthToken",$AuthToken) }
        $approverOut = & pwsh @approverArgs 2>&1 | Out-String
        $approverExit = $LASTEXITCODE

        # best-effort: директорский скрипт может "пропустить" эндпоинты и выйти 0
        if ($approverExit -ne 0) {
            Fail("Скрипт согласующего завершился с ошибкой (exit code=$approverExit). Вывод: `n$approverOut")
        }
        $stepsOk.Add("director $DirectorAction") | Out-Null

        # best-effort: финальный статус из вывода согласующего
        $approverStatus = Try-Parse $approverOut "статус платежа:\s*([^\s]+)\s*$"
        if ($approverStatus) { $finalStatus = $approverStatus }
    } else {
        # Resilient mode: если payment endpoint недоступен (404/405), не падаем — это норм для демо
        if ($noPaymentReason) {
            $stepsSkipped.Add("director $DirectorAction (payment api недоступен)") | Out-Null

            Write-Host ""
            Write-Host "=============================="
            Write-Host "ИТОГ ПИЛОТА (PARTIAL SUCCESS)" -ForegroundColor Yellow
            Write-Host "=============================="
            Write-Host ("- PDF: " + $PdfPath)
            Write-Host ("- TenantId: " + $TenantId)
            Write-Host ("- Лог бухгалтера: " + $logFile)
            Write-Host "- PaymentId: (не получен — payment API отсутствует/недоступен)"
            Write-Host "- Директор: шаг пропущен (нет PaymentId)"
            if ($accountantDone) { Write-Host ("- Бухгалтер (выполнено): " + $accountantDone) }
            if ($accountantSkipped) { Write-Host ("- Бухгалтер (пропущено): " + $accountantSkipped) }
            Write-Host ("- Шаги (OK): " + ($stepsOk -join ", "))
            Write-Host ("- Шаги (пропущено): " + ($stepsSkipped -join ", "))
            Write-Host "=============================="
            Write-Host ""
            Write-Host "PASS: демо завершено в режиме PARTIAL SUCCESS (payment API недоступен)." -ForegroundColor Yellow
            exit 0
        }

        # Реальная ошибка сценария: PaymentId не найден и нет явного маркера недоступности endpoint
        Fail("PaymentId не найден в выводе бухгалтерского скрипта. Проверьте лог: $logFile")
    }

    Write-Host ""
    Write-Host "=============================="
    Write-Host "ИТОГ ПИЛОТА"
    Write-Host "=============================="
    Write-Host ("- PDF: " + $PdfPath)
    Write-Host ("- TenantId: " + $TenantId)
    Write-Host ("- Лог бухгалтера: " + $logFile)
    if ($paymentId) {
        Write-Host ("- PaymentId: " + $paymentId)
    } else {
        Write-Host "- PaymentId: (не получен)"
    }
    if ($finalStatus) {
        Write-Host ("- Финальный статус платежа: " + $finalStatus)
    } else {
        Write-Host "- Финальный статус платежа: неизвестно (эндпоинт статуса мог отсутствовать)"
        $stepsSkipped.Add("payment status (unknown)") | Out-Null
    }
    if ($accountantDone) { Write-Host ("- Бухгалтер (выполнено): " + $accountantDone) }
    if ($accountantSkipped) { Write-Host ("- Бухгалтер (пропущено): " + $accountantSkipped) }
    Write-Host ("- Шаги (OK): " + ($stepsOk -join ", "))
    if ($stepsSkipped.Count -gt 0) {
        Write-Host ("- Шаги (пропущено): " + ($stepsSkipped -join ", "))
    }
    Write-Host "=============================="

    Write-Host ""
    Write-Host "PASS: пилот-демо завершено."
    exit 0
}
catch {
    Fail($_.Exception.Message)
}

