$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
python -m pip install -r requirements.txt pyinstaller
if ($LASTEXITCODE -ne 0) { throw 'Could not install build dependencies.' }
python -m PyInstaller --noconfirm --clean --onefile --windowed --name Workaholic --icon assets\workaholic.ico --add-data 'assets\workaholic.ico;assets' --distpath dist service_aggregator.py
if ($LASTEXITCODE -ne 0) { throw 'Build failed.' }
$appPath = Join-Path $PSScriptRoot 'Workaholic.exe'
if (Test-Path -LiteralPath $appPath) {
    $backupPath = Join-Path $PSScriptRoot ('Workaholic.previous-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff') + '.exe')
    Move-Item -LiteralPath $appPath -Destination $backupPath
}
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'dist\Workaholic.exe') -Destination $appPath
Write-Host "Built $PSScriptRoot\Workaholic.exe"
