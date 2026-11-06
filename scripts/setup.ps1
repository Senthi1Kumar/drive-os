# One-time setup for Windows 10/11 (PowerShell): creates .venv, installs DriveOS with voice, checks it.
#   powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\setup.ps1 -NoVoice
param([switch]$NoVoice)
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

# 1. find Python 3.11-3.13 through the py launcher
$py = $null
foreach ($v in "3.12", "3.13", "3.11") {
    try { & py "-$v" -c "import sys" 2>$null; if ($LASTEXITCODE -eq 0) { $py = @("py", "-$v"); break } } catch {}
}
if (-not $py) {
    Write-Error "Python 3.11, 3.12 or 3.13 is required. Install it from python.org (tick 'Add to PATH'), then re-run."
}
Write-Host "-> using Python $($py[1])"

# 2. virtual environment + packages
if (-not (Test-Path .venv)) { & $py[0] $py[1] -m venv .venv }
& .\.venv\Scripts\python.exe -m pip install --upgrade pip wheel | Out-Null
if ($NoVoice) {
    & .\.venv\Scripts\pip.exe install -e ".[gps,dev]"
} else {
    & .\.venv\Scripts\pip.exe install -e ".[voice,gps,dev]"
    & .\.venv\Scripts\pip.exe install --no-deps "openwakeword>=0.6,<0.7"
}

# 3. a local .env for your keys (all lines start commented out, so demo mode keeps working)
if (-not (Test-Path .env)) { Copy-Item .env.example .env }

# 4. check
& .\.venv\Scripts\driveos.exe doctor
Write-Host ""
if (-not (Test-Path .\.venv\Scripts\driveos.exe)) { Write-Error "install did not create .venv\Scripts\driveos.exe - see the pip output above" }
Write-Host "Done. Start DriveOS from $(Get-Location) with any of:"
Write-Host "  .\run.ps1                  # no activation needed"
Write-Host "  .\run.ps1 demo             # automated real-time walkthrough"
Write-Host "  .\run.ps1 --gps            # follow Windows Location (turn on Settings > Privacy > Location)"
Write-Host "Or: .\.venv\Scripts\Activate.ps1 ; driveos"
