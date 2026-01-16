<#
Ручной бухгалтерский прогон (best-effort) через локальный HTTP API.

Цель: загрузить реальный PDF (счёт), убедиться что документ виден в UI API,
и попытаться пройти минимальный платёжный контур (если эндпоинты доступны).

Без внешних сервисов, без OCR, без секретов.
#>

[CmdletBinding()]
Param(
    [Parameter(Mandatory = $false)]
    [string]$PdfPath
)

$ErrorActionPreference = "Stop"

try {
    $utf8 = [System.Text.UTF8Encoding]::new($false)
    [Console]::OutputEncoding = $utf8
    $OutputEncoding = $utf8
} catch {
    # best-effort
}

function Write-Step([string]$Text) {
    Write-Host ""
    Write-Host ("==> " + $Text)
}

function Write-Ok([string]$Text) {
    Write-Host ("OK: " + $Text)
}

function Write-Warn([string]$Text) {
    Write-Host ("Пропущено: " + $Text)
}

function Fail([string]$Text) {
    Write-Host ""
    Write-Host ("FAIL: " + $Text)
    exit 1
}

function Get-HttpStatus([string]$Url) {
    $code = & curl.exe -s -o NUL -w "%{http_code}" $Url
    return [int]$code
}

function Invoke-Json([string]$Method, [string]$Url, [hashtable]$Headers, [string]$BodyJson = $null) {
    $args = @("-s", "-X", $Method, $Url)
    foreach ($k in $Headers.Keys) {
        $args += @("-H", "${k}: $($Headers[$k])")
    }
    if ($BodyJson) {
        $args += @("-H", "Content-Type: application/json", "--data", $BodyJson)
    }
    $text = & curl.exe @args
    if ($LASTEXITCODE -ne 0) {
        throw "curl.exe завершился с ошибкой (exit code=$LASTEXITCODE)"
    }
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

$summaryDone = New-Object System.Collections.Generic.List[string]
$summarySkipped = New-Object System.Collections.Generic.List[string]

# Стабильные итоговые значения (best-effort)
$artifactId = $null
$documentId = $null
$caseId = $null
$paymentId = $null
$paymentStatus = $null
$paymentIdNote = $null
$paymentStatusNote = $null

try {
    if (-not $PdfPath) {
        Write-Host "Нужно указать путь к PDF."
        Write-Host "Пример запуска:"
        Write-Host "  pwsh -File scripts/manual-accountant-flow.ps1 -PdfPath ""C:\path\to\invoice.pdf"""
        exit 1
    }

    if (-not (Test-Path $PdfPath)) {
        Fail("Файл не найден: $PdfPath")
    }

    Write-Step "Проверка доступности сервиса (/health)"
    $healthCode = Get-HttpStatus "http://localhost:8000/health"
    if ($healthCode -lt 200 -or $healthCode -ge 300) {
        Fail("/health вернул HTTP $healthCode")
    }
    Write-Ok("/health (HTTP $healthCode)")
    $summaryDone.Add("/health") | Out-Null

    $tenantId = "tenant-manual"
    $headersTenant = @{ "X-Tenant-ID" = $tenantId }

    Write-Step "Загрузка PDF (POST /documents/upload)"
    $uploadRespText = & curl.exe -s -X POST "http://localhost:8000/documents/upload" `
        -H ("X-Tenant-ID: " + $tenantId) `
        -F ("file=@" + $PdfPath + ";type=application/pdf")
    if ($LASTEXITCODE -ne 0) {
        Fail("curl.exe upload завершился с ошибкой (exit code=$LASTEXITCODE)")
    }

    $uploadResp = $null
    try {
        $uploadResp = $uploadRespText | ConvertFrom-Json
    } catch {
        Fail("Не удалось распарсить JSON ответа upload")
    }

    $artifactId = $uploadResp.artifact_id
    $documentId = $uploadResp.document_id
    $docId = $artifactId
    if (-not $docId) { $docId = $documentId }
    if (-not $docId) { $docId = $uploadResp.id }
    if (-not $docId) {
        Fail("В ответе upload нет artifact_id/document_id")
    }
    Write-Ok("Документ загружен: id=$docId")
    $summaryDone.Add("upload PDF -> document id") | Out-Null

    Write-Step "Проверка списка документов (GET /api/v1/documents)"
    $docsText = Invoke-Json "GET" "http://localhost:8000/api/v1/documents?limit=20" $headersTenant
    $docs = $docsText | ConvertFrom-Json
    $found = $false
    foreach ($i in ($docs.items | ForEach-Object { $_ })) {
        if ($i.id -eq $docId) { $found = $true; break }
    }
    if (-not $found) {
        Fail("Загруженный документ не найден в /api/v1/documents")
    }
    Write-Ok("Документ найден в списке")
    $summaryDone.Add("document in list") | Out-Null

    Write-Step "Проверка деталей документа (GET /api/v1/documents/{id})"
    $detailText = Invoke-Json "GET" ("http://localhost:8000/api/v1/documents/" + $docId) $headersTenant
    $detail = $detailText | ConvertFrom-Json
    if ($detail.id -ne $docId) {
        Fail("Детали документа не совпали с ожидаемым id")
    }
    # best-effort: кейс/запись может присутствовать в ответе
    $caseId = $detail.case_id
    if (-not $caseId) { $caseId = $detail.caseId }
    if (-not $caseId -and $detail.case) { $caseId = $detail.case.id }
    Write-Ok("Детали документа доступны")
    $summaryDone.Add("document detail") | Out-Null

    # Платёжный контур (best-effort) — по OpenAPI
    Write-Step "Платёжный контур (best-effort, по OpenAPI)"
    $paths = Get-OpenApiPaths
    if (-not $paths) {
        Write-Warn("не удалось получить /openapi.json, пропускаю payment шаги")
        $summarySkipped.Add("payment flow (openapi unavailable)") | Out-Null
        if (-not $paymentIdNote) { $paymentIdNote = "PaymentId не получен: эндпоинт создания/поиска платежа отсутствует в openapi" }
    }
    else {
        # 1) create payment order
        if (-not (Has-OpenApiPath $paths "/api/v1/payments/orders" "post")) {
            Write-Warn("create payment order: эндпоинт /api/v1/payments/orders (POST) не найден")
            $summarySkipped.Add("create payment order") | Out-Null
            if (-not $paymentIdNote) { $paymentIdNote = "PaymentId не получен: эндпоинт создания/поиска платежа отсутствует в openapi" }
        }
        else {
            try {
                # Минимальный валидный payload (не привязываем к OCR/инвойсу).
                $payload = @{
                    amount = 1000
                    beneficiary_name = "Тестовый получатель"
                    beneficiary_account_iban = "KZ000000000000000000"
                    purpose = "Тестовый платеж (manual flow)"
                    created_by_role = "accountant"
                    currency = "KZT"
                } | ConvertTo-Json

                $orderText = Invoke-Json "POST" "http://localhost:8000/api/v1/payments/orders" $headersTenant $payload
                $order = $orderText | ConvertFrom-Json
                $orderId = $order.id
                if (-not $orderId) { throw "нет id в ответе create_payment_order" }
                $paymentId = $orderId
                Write-Ok("PaymentOrder создан: id=$orderId")
                $summaryDone.Add("create payment order") | Out-Null

                # 2) submit for approval
                if (-not (Has-OpenApiPath $paths "/api/v1/payments/orders/{order_id}/submit" "post")) {
                    Write-Warn("submit: эндпоинт не найден")
                    $summarySkipped.Add("submit for approval") | Out-Null
                } else {
                    try {
                        $submitText = Invoke-Json "POST" ("http://localhost:8000/api/v1/payments/orders/" + $orderId + "/submit") $headersTenant
                        $submit = $submitText | ConvertFrom-Json
                        Write-Ok("submit: " + ($submit.message ?? "ok"))
                        $summaryDone.Add("submit for approval") | Out-Null
                    } catch {
                        Write-Warn("submit: ошибка, пропускаю (" + $_.Exception.Message + ")")
                        $summarySkipped.Add("submit for approval (failed)") | Out-Null
                    }
                }

                # 3) approve (best-effort: роль accountant)
                if (-not (Has-OpenApiPath $paths "/api/v1/payments/orders/{order_id}/approve" "post")) {
                    Write-Warn("approve: эндпоинт не найден")
                    $summarySkipped.Add("approve") | Out-Null
                } else {
                    try {
                        $approvePayload = @{ role = "accountant"; comment = "manual flow"; decided_by = "manual-script" } | ConvertTo-Json
                        $approveText = Invoke-Json "POST" ("http://localhost:8000/api/v1/payments/orders/" + $orderId + "/approve") $headersTenant $approvePayload
                        $approve = $approveText | ConvertFrom-Json
                        Write-Ok("approve: " + ($approve.message ?? "ok"))
                        $summaryDone.Add("approve") | Out-Null
                    } catch {
                        Write-Warn("approve: ошибка, пропускаю (" + $_.Exception.Message + ")")
                        $summarySkipped.Add("approve (failed)") | Out-Null
                    }
                }

                # 4) export (csv)
                if (-not (Has-OpenApiPath $paths "/api/v1/payments/orders/{order_id}/export" "post")) {
                    Write-Warn("export: эндпоинт не найден")
                    $summarySkipped.Add("export") | Out-Null
                } else {
                    try {
                        $exportPayload = @{ format = "csv" } | ConvertTo-Json
                        $exportText = Invoke-Json "POST" ("http://localhost:8000/api/v1/payments/orders/" + $orderId + "/export") $headersTenant $exportPayload
                        $export = $exportText | ConvertFrom-Json
                        Write-Ok("export: " + ($export.message ?? "ok"))
                        $summaryDone.Add("export") | Out-Null
                    } catch {
                        Write-Warn("export: ошибка, пропускаю (" + $_.Exception.Message + ")")
                        $summarySkipped.Add("export (failed)") | Out-Null
                    }
                }
            }
            catch {
                Write-Warn("payment flow: не удалось выполнить (ошибка: " + $_.Exception.Message + ")")
                $summarySkipped.Add("payment flow (failed)") | Out-Null
                if (-not $paymentId -and -not $paymentIdNote) { $paymentIdNote = "PaymentId не получен: не удалось создать платеж (ошибка: $($_.Exception.Message))" }
            }
        }
    }

    # best-effort: получить статус платежа, если доступно
    if ($paths -and $paymentId -and (Has-OpenApiPath $paths "/api/v1/payments/orders/{order_id}" "get")) {
        try {
            $pText = Invoke-Json "GET" ("http://localhost:8000/api/v1/payments/orders/" + $paymentId) $headersTenant
            $p = $pText | ConvertFrom-Json
            $paymentStatus = $p.status
            if (-not $paymentStatus) { $paymentStatus = $p.state }
            if (-not $paymentStatus) { $paymentStatusNote = "Статус платежа получен, но поле status отсутствует" }
        } catch {
            $paymentStatusNote = "Не удалось получить статус платежа: $($_.Exception.Message)"
        }
    } elseif (-not $paymentId) {
        if (-not $paymentIdNote) { $paymentIdNote = "PaymentId не получен: эндпоинт создания/поиска платежа отсутствует в openapi" }
    } elseif (-not $paths -or -not (Has-OpenApiPath $paths "/api/v1/payments/orders/{order_id}" "get")) {
        $paymentStatusNote = "Статус платежа не получен: эндпоинт чтения платежа отсутствует в openapi"
    }

    Write-Host ""
    Write-Host "РЕЗЮМЕ"
    Write-Host ("- Выполнено: " + ($summaryDone -join ", "))
    if ($summarySkipped.Count -gt 0) {
        Write-Host ("- Пропущено: " + ($summarySkipped -join ", "))
    } else {
        Write-Host "- Пропущено: нет"
    }

    Write-Host ""
    Write-Host "-----"
    Write-Host "СТАБИЛЬНЫЙ ВЫВОД:"
    Write-Host ("ArtifactId: " + ($(if ($artifactId) { $artifactId } else { "(нет)" })))
    Write-Host ("DocumentId: " + ($(if ($documentId) { $documentId } else { "(нет)" })))
    Write-Host ("CaseId: " + ($(if ($caseId) { $caseId } else { "(нет)" })))
    if ($paymentId) {
        Write-Host ("PaymentId: " + $paymentId)
    } else {
        Write-Host ($paymentIdNote ?? "PaymentId не получен")
    }
    if ($paymentStatus) {
        Write-Host ("Статус платежа: " + $paymentStatus)
    } else {
        Write-Host ("Статус платежа: " + ($paymentStatusNote ?? "неизвестно"))
    }
    Write-Host "-----"
    Write-Host "ИТОГ ДЛЯ ПЕРЕДАЧИ ДИРЕКТОРУ:"
    if ($paymentId) {
        Write-Host ("PaymentId: " + $paymentId)
    } else {
        Write-Host ($paymentIdNote ?? "PaymentId не получен")
    }
    Write-Host "-----"
    Write-Host ""
    Write-Host "PASS: ручной бухгалтерский прогон завершён."
    exit 0
}
catch {
    Fail($_.Exception.Message)
}

