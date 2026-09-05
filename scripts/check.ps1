# check.ps1 — Run all backend checks (syntax + ruff lint)
# Usage: .\scripts\check.ps1

$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
$failed = $false

Write-Host ""
Write-Host "Running backend checks..." -ForegroundColor Yellow
Write-Host ""

# 1. Python syntax check
Write-Host "[1/2] Python syntax check..." -ForegroundColor Yellow
$pyFiles = @(
    "$root\main.py",
    "$root\database.py",
    "$root\models.py",
    "$root\services\tts.py",
    "$root\services\translation.py"
)
foreach ($f in $pyFiles) {
    $null = python -m py_compile $f 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  FAIL: $f" -ForegroundColor Red
        $failed = $true
    }
}
if (-not $failed) { Write-Host "  OK Python syntax" -ForegroundColor Green }

# 2. Ruff lint
Write-Host ""
Write-Host "[2/2] Ruff lint..." -ForegroundColor Yellow
Push-Location $root
ruff check . --config pyproject.toml 2>&1
if ($LASTEXITCODE -ne 0) { $failed = $true }
Pop-Location

Write-Host ""
if ($failed) {
    Write-Host "Backend checks FAILED." -ForegroundColor Red
    exit 1
} else {
    Write-Host "All backend checks passed!" -ForegroundColor Green
    exit 0
}
