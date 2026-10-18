$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvPath = Join-Path $ProjectRoot ".venv-light"
$PythonExe = Join-Path $VenvPath "Scripts\python.exe"

# Create clean dedicated lightweight venv
if (-not (Test-Path $VenvPath)) {
  Write-Host "Creating fresh lightweight venv at .venv-light..."
  py -3 -m venv $VenvPath
}

& $PythonExe -m pip install --upgrade pip
& $PythonExe -m pip install -r (Join-Path $ProjectRoot "requirements-build.txt")

Write-Host "Starting PyInstaller build..."
& $PythonExe -m PyInstaller `
  --onefile `
  --noconsole `
  --name python `
  --clean `
  --icon (Join-Path $ProjectRoot "python_icon.ico") `
  --exclude-module matplotlib `
  --exclude-module scipy `
  --exclude-module pandas `
  --exclude-module numpy `
  --exclude-module IPython `
  --exclude-module cv2 `
  --exclude-module grpc `
  --exclude-module google `
  --exclude-module unittest `
  --exclude-module pytest `
  --distpath (Join-Path $ProjectRoot "temp_build\dist") `
  --workpath (Join-Path $ProjectRoot "temp_build\build") `
  --specpath (Join-Path $ProjectRoot "temp_build") `
  (Join-Path $ProjectRoot "omni_assistant.py")

$BuiltExe = Join-Path $ProjectRoot "temp_build\dist\python.exe"
$DestExe = Join-Path $ProjectRoot "python.exe"

if (Test-Path $BuiltExe) {
  Move-Item -Force $BuiltExe $DestExe
  $SizeMB = [math]::Round((Get-Item $DestExe).Length / 1MB, 2)
  Write-Host "SUCCESS: Built $DestExe with size: $SizeMB MB"
}

# Cleanup build artifacts and light venv
Remove-Item -Recurse -Force (Join-Path $ProjectRoot "temp_build") -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force $VenvPath -ErrorAction SilentlyContinue
