param([string]$Python = '', [string]$Config = '')
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
if (-not $Python) {
    $found = Get-Command python -ErrorAction SilentlyContinue
    if ($found) { $Python = $found.Source }
    else { $Python = 'C:\Program Files\LibreOffice\program\python.exe' }
}
if (-not (Test-Path -LiteralPath $Python)) {
    throw "Python not found. Pass -Python <path-to-python.exe>."
}
$deps = Join-Path $repo '.ground_station_deps'
$env:PYTHONPATH = "$deps;$repo"
& $Python -c 'import PySide6' 2>$null
if ($LASTEXITCODE -ne 0) {
    throw "PySide6 missing. Run: & '$Python' -m pip install --target '$deps' PySide6==6.11.2"
}
Set-Location -LiteralPath $repo
if ($Config) { & $Python -m ground_station.app --config $Config }
else { & $Python -m ground_station.app }
exit $LASTEXITCODE
