param([switch]$Release, [switch]$SkipInstall)

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not $SkipInstall) {
    python -m pip install -r requirements-build.txt
    if ($LASTEXITCODE -ne 0) { throw 'Could not install build dependencies.' }
}
python scripts\package_release.py version-info
if ($LASTEXITCODE -ne 0) { throw 'Could not generate version information.' }
python -m PyInstaller --noconfirm --clean --onefile --windowed --noupx --name Workaholic --icon assets\workaholic.ico --add-data 'assets\workaholic.ico;assets' --add-data 'licenses;licenses' --add-data 'THIRD-PARTY-NOTICES.md;.' --version-file build\version-info.txt --distpath dist service_aggregator.py
if ($LASTEXITCODE -ne 0) { throw 'Build failed.' }
if ($Release) {
    python scripts\package_release.py package
    if ($LASTEXITCODE -ne 0) { throw 'Release packaging failed.' }
    return
}
$appPath = Join-Path $PSScriptRoot 'Workaholic.exe'
if (Test-Path -LiteralPath $appPath) {
    $backupPath = Join-Path $PSScriptRoot ('Workaholic.previous-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff') + '.exe')
    Move-Item -LiteralPath $appPath -Destination $backupPath
}
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'dist\Workaholic.exe') -Destination $appPath
Write-Host "Built $PSScriptRoot\Workaholic.exe"
