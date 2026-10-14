$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
python -m pip install -r requirements.txt pyinstaller
if ($LASTEXITCODE -ne 0) { throw 'Could not install build dependencies.' }
python -m PyInstaller --noconfirm --clean --onefile --windowed --name Workaholic --distpath . service_aggregator.py
if ($LASTEXITCODE -ne 0) { throw 'Build failed.' }
Write-Host "Built $PSScriptRoot\Workaholic.exe"
