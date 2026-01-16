<#
Ручной прогон согласующего (директор) — approve/reject payment order.

Best-effort:
- если нужных эндпоинтов нет в OpenAPI, печатает сообщение и выходит с exit code 0
- если входные параметры некорректны, exit code 1

Без UI, без внешних сервисов, без секретов.
#>

[CmdletBinding()]
Param(
    [Parameter(Mandatory = $false)]
    [string]$PaymentId,

    [Parameter(Mandatory = $false)]
    [ValidateSet("approve","reject")]
    [string]$Action = "approve",

    [Parameter(Mandatory = $false)]
    [string]$Reason = "Отклонено для теста"
)

$ErrorActionPreference = "Stop"

try {
    $utf8 = [System.Text.UTF8Encoding]::new($false)
    [Console]::OutputEncoding = $utf8
    $OutputEncoding = $utf8
} catch {
    # best-effort
}

function Write-Step([string]$Text) { Write-Host ""; Write-Host ("==> " + $Text) }
function Write-Ok([string]$Text) { Write-Host ("OK: " + $Text) }
function Write-Warn([string]$Text) { Write-Host ("Пропущено: " + $Text) }
function Fail([string]$Text) { Write-Host ""; Write-Host ("FAIL: " + $Text); exit 1 }

function Get-HttpStatus([string]$Url) {
    $code = & curl.exe -s -o NUL -w "%{http_code}" $Url
    return [int]$code
}

function Invoke-Json([string]$Method, [string]$Url, [hashtable]$Headers, [string]$BodyJson = $null) {
    $args = @("-s", "-X", $Method, $Url)
    foreach ($k in $Headers.Keys) { $args += @("-H", "${k}: $($Headers[$k])") }
    if ($BodyJson) { $args += @("-H", "Content-Type: application/json", "--data", $BodyJson) }
    $text = & curl.exe @args
    if ($LASTEXITCODE -ne 0) { throw "curl.exe завершился с ошибкой (exit code=$LASTEXITCODE)" }
    return $text
}

function Get-OpenApiPaths() {
    try {
        $raw = Invoke-Json "GET" "http://localhost:8000/openapi.json" @{}
        $obj = $raw | ConvertFrom-Json
        return $obj.paths
    } catch {
        return $null
    }
}

function Has-OpenApiPath($paths, [string]$Path, [string]$Method) {
    if (-not $paths) { return $false }
    $p = $paths.$Path
    if (-not $p) { return $false }
    return ($p.PSObject.Properties.Name -contains $Method.ToLower())
}

$done = New-Object System.Collections.Generic.List[string]
$skipped = New-Object System.Collections.Generic.List[string]

try {
    if (-not $PaymentId) {
        Write-Host "Нужно указать PaymentId (ID платёжного поручения)."
        Write-Host "Пример:"
        Write-Host "  pwsh -File scripts/manual-approver-flow.ps1 -PaymentId ""<order_id>"" -Action approve"
        Write-Host "  pwsh -File scripts/manual-approver-flow.ps1 -PaymentId ""<order_id>"" -Action reject -Reason ""Недостаточно оснований"""
        exit 1
    }

    Write-Step "Проверка доступности сервиса (/health)"
    $healthCode = Get-HttpStatus "http://localhost:8000/health"
    if ($healthCode -lt 200 -or $healthCode -ge 300) { Fail("/health вернул HTTP $healthCode") }
    Write-Ok("/health (HTTP $healthCode)")
    $done.Add("/health") | Out-Null

    $tenantId = "tenant-manual"
    $headersTenant = @{ "X-Tenant-ID" = $tenantId }

    Write-Step "Проверка OpenAPI"
    $paths = Get-OpenApiPaths
    if (-not $paths) {
        Write-Warn("не удалось получить /openapi.json, пропускаю (best-effort)")
        $skipped.Add("openapi unavailable") | Out-Null
        Write-Host ""
        Write-Host "РЕЗЮМЕ"
        Write-Host ("- Выполнено: " + ($done -join ", "))
        Write-Host ("- Пропущено: " + ($skipped -join ", "))
        Write-Host ""
        Write-Host "PASS: шаг согласования пропущен (эндпоинты не обнаружены)."
        exit 0
    }
    Write-Ok("OpenAPI загружен")
    $done.Add("openapi") | Out-Null

    $approvePath = "/api/v1/payments/orders/{order_id}/approve"
    $rejectPath = "/api/v1/payments/orders/{order_id}/reject"
    $getPath = "/api/v1/payments/orders/{order_id}"

    if ($Action -eq "approve") {
        if (-not (Has-OpenApiPath $paths $approvePath "post")) {
            Write-Warn("approve: эндпоинт не найден ($approvePath)")
            $skipped.Add("approve endpoint") | Out-Null
            exit 0
        }
        Write-Step "approve (директор)"
        $payload = @{ role = "director"; comment = "manual approve"; decided_by = "manual-script" } | ConvertTo-Json
        $respText = Invoke-Json "POST" ("http://localhost:8000/api/v1/payments/orders/" + $PaymentId + "/approve") $headersTenant $payload
        try {
            $resp = $respText | ConvertFrom-Json
            Write-Ok("approve: " + ($resp.message ?? "ok"))
        } catch {
            Write-Ok("approve: выполнено")
        }
        $done.Add("approve") | Out-Null
    }
    elseif ($Action -eq "reject") {
        if (-not (Has-OpenApiPath $paths $rejectPath "post")) {
            Write-Warn("reject: эндпоинт не найден ($rejectPath)")
            $skipped.Add("reject endpoint") | Out-Null
            exit 0
        }
        Write-Step "reject (директор)"
        $payload = @{ role = "director"; comment = $Reason; decided_by = "manual-script" } | ConvertTo-Json
        $respText = Invoke-Json "POST" ("http://localhost:8000/api/v1/payments/orders/" + $PaymentId + "/reject") $headersTenant $payload
        try {
            $resp = $respText | ConvertFrom-Json
            Write-Ok("reject: " + ($resp.message ?? "ok"))
        } catch {
            Write-Ok("reject: выполнено")
        }
        $done.Add("reject") | Out-Null
    }

    Write-Step "Проверка статуса платежа (GET)"
    if (-not (Has-OpenApiPath $paths $getPath "get")) {
        Write-Warn("get payment: эндпоинт не найден ($getPath)")
        $skipped.Add("get payment") | Out-Null
    } else {
        try {
            $orderText = Invoke-Json "GET" ("http://localhost:8000/api/v1/payments/orders/" + $PaymentId) $headersTenant
            $order = $orderText | ConvertFrom-Json
            $status = $order.status
            if ($status) {
                Write-Ok("статус платежа: $status")
            } else {
                Write-Ok("статус платежа получен")
            }
            $done.Add("get status") | Out-Null
        } catch {
            Write-Warn("не удалось получить статус платежа (" + $_.Exception.Message + ")")
            $skipped.Add("get status (failed)") | Out-Null
        }
    }

    Write-Host ""
    Write-Host "РЕЗЮМЕ"
    Write-Host ("- Выполнено: " + ($done -join ", "))
    if ($skipped.Count -gt 0) { Write-Host ("- Пропущено: " + ($skipped -join ", ")) } else { Write-Host "- Пропущено: нет" }

    Write-Host ""
    Write-Host "PASS: прогон согласующего завершён."
    exit 0
}
catch {
    Fail($_.Exception.Message)
}

