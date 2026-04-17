$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvPath = Join-Path $ProjectRoot ".venv-build"
$PythonExe = Join-Path $VenvPath "Scripts\python.exe"

if (-not (Test-Path $VenvPath)) {
  py -3 -m venv $VenvPath
}

& $PythonExe -m pip install --upgrade pip setuptools wheel
& $PythonExe -m pip install -r (Join-Path $ProjectRoot "requirements-build.txt")

& $PythonExe -m PyInstaller `
  --onefile `
  --noconsole `
  --name python `
  --clean `
  --icon (Join-Path $ProjectRoot "python_icon.ico") `
  --distpath (Join-Path $ProjectRoot "temp_build\dist") `
  --workpath (Join-Path $ProjectRoot "temp_build\build") `
  --specpath (Join-Path $ProjectRoot "temp_build") `
  (Join-Path $ProjectRoot "omni_assistant.py")
