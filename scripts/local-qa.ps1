<# 
Локальный QA-ритуал для CyberPlat (Windows / PowerShell 7).

Требования:
- Не удаляет данные (только поднимает compose и выполняет проверки).
- Сообщения на русском.
- ExitCode 0 = PASS, 1 = FAIL.
#>

[CmdletBinding()]
Param(
    [Parameter(Mandatory = $false)]
    [string]$TenantId = "demo-tenant"
)

$ErrorActionPreference = "Stop"

# Корректный вывод русских сообщений в консоль (Windows Terminal / PowerShell 7).
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

function Fail([string]$Text) {
    Write-Host ""
    Write-Host ("FAIL: " + $Text)
    exit 1
}

function Assert-LastExitCode([string]$What) {
    if ($LASTEXITCODE -ne 0) {
        Fail("$What (exit code=$LASTEXITCODE)")
    }
}

function Get-HttpStatus([string]$Url) {
    # curl.exe: вернёт только HTTP code
    $code = & curl.exe -s -o NUL -w "%{http_code}" $Url
    return [int]$code
}

try {
    # cd в корень репо (относительно location скрипта)
    $repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
    Set-Location $repoRoot

    Write-Host "Локальный QA: CyberPlat"
    Write-Host ("Корень репозитория: " + $repoRoot)

    Write-Step "docker compose up -d --build"
    & docker compose up -d --build
    Assert-LastExitCode "docker compose up"

    Write-Step "docker compose ps"
    & docker compose ps
    Assert-LastExitCode "docker compose ps"

    Write-Step "pytest (в контейнере app)"
    & docker compose exec -T app python -m pytest -q --tb=short
    Assert-LastExitCode "pytest"

    Write-Step "ruff check . (в контейнере app)"
    & docker compose exec -T app ruff check .
    Assert-LastExitCode "ruff check"

    Write-Step "/health"
    $healthCode = Get-HttpStatus "http://localhost:8000/health"
    if ($healthCode -lt 200 -or $healthCode -ge 300) {
        Fail("/health вернул HTTP $healthCode")
    }
    Write-Host ("OK: /health (HTTP " + $healthCode + ")")

    Write-Step "/metrics"
    $metricsCode = Get-HttpStatus "http://localhost:8000/metrics"
    if ($metricsCode -lt 200 -or $metricsCode -ge 300) {
        Fail("/metrics вернул HTTP $metricsCode")
    }
    Write-Host ("OK: /metrics (HTTP " + $metricsCode + ")")

    Write-Step "Smoke upload PDF (опционально)"
    $fixturesDir = Join-Path $repoRoot "tests\fixtures"
    $pdfFixture = $null
    if (Test-Path $fixturesDir) {
        $pdfFixture = Get-ChildItem -Path $fixturesDir -Filter "*.pdf" -File -ErrorAction SilentlyContinue | Select-Object -First 1
    }

    if (-not $pdfFixture) {
        Write-Host "Нет PDF файла в tests/fixtures/*.pdf — пропускаю smoke upload."
    }
    else {
        $tenantId = $TenantId
        Write-Host ("Использую fixture: " + $pdfFixture.FullName)
        $respText = & curl.exe -s -X POST "http://localhost:8000/documents/upload" `
            -H ("X-Tenant-ID: " + $tenantId) `
            -F ("file=@" + $pdfFixture.FullName + ";type=application/pdf")
        if ($LASTEXITCODE -ne 0) {
            Fail("Smoke upload: curl.exe завершился с ошибкой (exit code=$LASTEXITCODE)")
        }

        try {
            $resp = $respText | ConvertFrom-Json
            $artifactId = $resp.artifact_id
            if (-not $artifactId) {
                Fail("Smoke upload: в ответе нет artifact_id")
            }
            Write-Host ("OK: upload artifact_id=" + $artifactId)

            $docsCode = Get-HttpStatus "http://localhost:8000/api/v1/documents?limit=20"
            # Этот endpoint требует X-Tenant-ID, поэтому делаем полноценный запрос:
            $docsText = & curl.exe -s "http://localhost:8000/api/v1/documents?limit=20" -H ("X-Tenant-ID: " + $tenantId)
            $docs = $docsText | ConvertFrom-Json
            $items = $docs.items
            $found = $false
            foreach ($i in $items) {
                if ($i.id -eq $artifactId) { $found = $true; break }
            }
            if (-not $found) {
                Fail("Smoke upload: документ не найден в /api/v1/documents")
            }
            Write-Host "OK: документ найден в списке /api/v1/documents"
        }
        catch {
            Fail("Smoke upload: не удалось распарсить JSON ответа")
        }
    }

    Write-Host ""
    Write-Host "PASS: Локальный QA успешно завершён."
    Write-Host "Примечание: контейнеры остаются запущенными (docker compose ps)."
    exit 0
}
catch {
    Fail($_.Exception.Message)
}

