# Start DriveOS without activating the virtualenv. Arguments go to `driveos` (e.g. .\run.ps1 demo).
$ErrorActionPreference = "Stop"
$dir = Split-Path -Parent $MyInvocation.MyCommand.Path
$exe = Join-Path $dir ".venv\Scripts\driveos.exe"
if (-not (Test-Path $exe)) {
    Write-Host "DriveOS isn't installed yet - running scripts\setup.ps1 first..."
    & (Join-Path $dir "scripts\setup.ps1")
}
Set-Location $dir
& $exe @args
exit $LASTEXITCODE
