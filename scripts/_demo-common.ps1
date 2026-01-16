function Get-RepoRoot() {
    # scripts/ -> repo root
    return (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
}

function Get-DefaultTenantId() {
    return "demo-tenant"
}

function Get-DefaultDemoPdfPath() {
    return (Join-Path (Get-RepoRoot) "demo\demo-invoice.pdf")
}

function Write-Section([string]$Text) {
    Write-Host ""
    Write-Host ("==> " + $Text)
}

function Write-Ok([string]$Text) {
    Write-Host ("OK: " + $Text) -ForegroundColor Green
}

function Write-Warn([string]$Text) {
    Write-Host ("WARN: " + $Text) -ForegroundColor Yellow
}

function New-ApiHeaders(
    [string]$TenantId,
    [string]$Role = "",
    [string]$AuthToken = ""
) {
    $h = @{ "X-Tenant-ID" = $TenantId }
    if ($Role) { $h["X-Role"] = $Role }
    if ($AuthToken) { $h["Authorization"] = ("Bearer " + $AuthToken) }
    return $h
}

