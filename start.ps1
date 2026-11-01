$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not (Test-Path -LiteralPath '.venv/Scripts/python.exe')) {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the Python environment.' }
}
& ./.venv/Scripts/python.exe -m pip install --require-hashes -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
& ./.venv/Scripts/python.exe -m playwright install chromium
if ($LASTEXITCODE -ne 0) { throw 'Browser installation failed.' }
& ./.venv/Scripts/python.exe app.py --prepare-model
if ($LASTEXITCODE -ne 0) { throw 'Model preparation failed.' }
& ./.venv/Scripts/python.exe app.py
