<#
Ручной бухгалтерский прогон (best-effort) через локальный HTTP API.

Цель: загрузить реальный PDF (счёт), убедиться что документ виден в UI API,
и попытаться пройти минимальный платёжный контур (если эндпоинты доступны).

Без внешних сервисов, без OCR, без секретов.
#>

[CmdletBinding()]
Param(
    [Parameter(Mandatory = $false)]
    [string]$PdfPath,

    [Parameter(Mandatory = $false)]
    [string]$TenantId = "demo-tenant"
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

function Invoke-JsonWithStatus(
    [string]$Method,
    [string]$Url,
    [hashtable]$Headers,
    [string]$BodyJson = $null,
    [int]$TimeoutSeconds = 20
) {
    $tmp = [System.IO.Path]::GetTempFileName()
    try {
        $args = @("-s", "--max-time", "$TimeoutSeconds", "-o", $tmp, "-w", "%{http_code}", "-X", $Method, $Url)
        foreach ($k in $Headers.Keys) { $args += @("-H", "${k}: $($Headers[$k])") }
        if ($BodyJson) { $args += @("-H", "Content-Type: application/json", "--data", $BodyJson) }

        $statusText = & curl.exe @args
        if ($LASTEXITCODE -ne 0) { throw "curl.exe завершился с ошибкой (exit code=$LASTEXITCODE)" }
        $status = [int]$statusText

        $bodyBytes = [System.IO.File]::ReadAllBytes($tmp)
        $body = [System.Text.Encoding]::UTF8.GetString($bodyBytes)
        return @{ status = $status; body = $body }
    } finally {
        try { Remove-Item -Force $tmp -ErrorAction SilentlyContinue } catch { }
    }
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
    # Корень репо (на уровень выше scripts/)
    $repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

    if (-not $PdfPath) {
        $PdfPath = (Join-Path $repoRoot "demo\demo-invoice.pdf")
    }

    if (-not (Test-Path $PdfPath)) {
        Fail("PDF файл не найден: $PdfPath. Укажите -PdfPath или сгенерируйте demo PDF через scripts/gen-demo-invoice-pdf.py")
    }

    Write-Step "Проверка доступности сервиса (/health)"
    $healthCode = Get-HttpStatus "http://localhost:8000/health"
    if ($healthCode -lt 200 -or $healthCode -ge 300) {
        Fail("/health вернул HTTP $healthCode")
    }
    Write-Ok("/health (HTTP $healthCode)")
    $summaryDone.Add("/health") | Out-Null

    $headersTenant = @{ "X-Tenant-ID" = $TenantId }

    Write-Step "Загрузка PDF (POST /documents/upload)"
    $uploadRespText = & curl.exe -s -X POST "http://localhost:8000/documents/upload" `
        -H ("X-Tenant-ID: " + $TenantId) `
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

    # Платёжный контур (best-effort, без зависимости от OpenAPI)
    Write-Step "Платёжный контур (best-effort)"
    try {
        $payloadObj = @{
            amount = 1000
            beneficiary_name = "Тестовый получатель"
            beneficiary_account_iban = "KZ000000000000000000"
            purpose = "Тестовый платеж (manual flow)"
            created_by_role = "accountant"
            currency = "KZT"
        }
        if ($caseId) { $payloadObj.case_id = $caseId }
        $payload = $payloadObj | ConvertTo-Json

        $create = Invoke-JsonWithStatus "POST" "http://localhost:8000/api/v1/payments/orders" $headersTenant $payload 25
        if ($create.status -eq 404 -or $create.status -eq 405) {
            Write-Warn("create payment order: endpoint недоступен (HTTP $($create.status))")
            $summarySkipped.Add("create payment order (unavailable)") | Out-Null
            $paymentIdNote = "PaymentId не получен: endpoint недоступен (HTTP $($create.status))"
        }
        elseif ($create.status -ge 200 -and $create.status -lt 300) {
            $order = $null
            try {
                $order = ($create.body | ConvertFrom-Json)
            } catch {
                Fail("create payment order: ответ 2xx, но JSON не парсится (это поломка пилота)")
            }
            $orderId = $order.id
            if (-not $orderId) {
                Fail("create payment order: ответ 2xx, но нет поля id (это поломка пилота)")
            }
            $paymentId = $orderId
            Write-Ok("PaymentOrder создан: id=$paymentId")
            $summaryDone.Add("create payment order") | Out-Null

            # submit for approval (роль бухгалтера)
            $submit = Invoke-JsonWithStatus "POST" ("http://localhost:8000/api/v1/payments/orders/" + $paymentId + "/submit") $headersTenant $null 25
            if ($submit.status -eq 404 -or $submit.status -eq 405) {
                Write-Warn("submit: endpoint недоступен (HTTP $($submit.status))")
                $summarySkipped.Add("submit for approval (unavailable)") | Out-Null
            }
            elseif ($submit.status -ge 200 -and $submit.status -lt 300) {
                Write-Ok("submit: ok")
                $summaryDone.Add("submit for approval") | Out-Null
            }
            elseif ($submit.status -ge 500) {
                Fail("submit: серверная ошибка (HTTP $($submit.status)) — это поломка пилота")
            }
            else {
                Fail("submit: ошибка (HTTP $($submit.status)) — проверьте контракт API. Body: $($submit.body)")
            }
        }
        elseif ($create.status -ge 500) {
            Fail("create payment order: серверная ошибка (HTTP $($create.status)) — это поломка пилота")
        }
        else {
            Fail("create payment order: ошибка (HTTP $($create.status)) — проверьте контракт API. Body: $($create.body)")
        }
    }
    catch {
        # Curl timeout / transport error => FAIL (это поломка пилота)
        Fail("payment flow: ошибка выполнения (" + $_.Exception.Message + ")")
    }

    # best-effort: получить статус платежа (если endpoint есть)
    if ($paymentId) {
        $getP = Invoke-JsonWithStatus "GET" ("http://localhost:8000/api/v1/payments/orders/" + $paymentId) $headersTenant $null 20
        if ($getP.status -eq 404 -or $getP.status -eq 405) {
            $paymentStatusNote = "Статус платежа не получен: endpoint недоступен (HTTP $($getP.status))"
        }
        elseif ($getP.status -ge 200 -and $getP.status -lt 300) {
            try {
                $p = ($getP.body | ConvertFrom-Json)
                $paymentStatus = $p.status
                if (-not $paymentStatus) { $paymentStatus = $p.state }
                if (-not $paymentStatus) { $paymentStatusNote = "Статус платежа получен, но поле status отсутствует" }
            } catch {
                Fail("get payment: ответ 2xx, но JSON не парсится (это поломка пилота)")
            }
        }
        elseif ($getP.status -ge 500) {
            Fail("get payment: серверная ошибка (HTTP $($getP.status)) — это поломка пилота")
        }
        else {
            Fail("get payment: ошибка (HTTP $($getP.status)) — проверьте контракт API. Body: $($getP.body)")
        }
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

