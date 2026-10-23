$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvPath = Join-Path $ProjectRoot ".venv-light"
$PythonExe = Join-Path $VenvPath "Scripts\python.exe"

$DestExe = Join-Path $ProjectRoot "python.exe"

# Stop any python/sistem processes holding venv or exe
Get-Process python, sistem -ErrorAction SilentlyContinue | Where-Object { 
  $_.Path -and ($_.Path -like "*\.venv-light\*" -or $_.Path -like "*\sistem.exe" -or $_.Path -like "*\python.exe")
} | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Milliseconds 500

# Create clean dedicated lightweight venv
$PyVenvCfg = Join-Path $VenvPath "pyvenv.cfg"
if (-not (Test-Path $PyVenvCfg)) {
  Write-Host "Creating fresh lightweight venv at .venv-light..."
  Remove-Item -Recurse -Force $VenvPath -ErrorAction SilentlyContinue
  py -3 -m venv $VenvPath
}

& $PythonExe -m pip install --upgrade pip
& $PythonExe -m pip install -r (Join-Path $ProjectRoot "requirements-build.txt")

Write-Host "Starting PyInstaller build for sistem.exe..."
& $PythonExe -m PyInstaller `
  --onefile `
  --noconsole `
  --name sistem `
  --clean `
  --icon (Join-Path $ProjectRoot "system_icon.ico") `
  --version-file (Join-Path $ProjectRoot "version_info.txt") `
  --exclude-module matplotlib `
  --exclude-module scipy `
  --exclude-module pandas `
  --exclude-module IPython `
  --exclude-module grpc `
  --exclude-module google `
  --exclude-module unittest `
  --exclude-module pytest `
  --distpath (Join-Path $ProjectRoot "temp_build\dist") `
  --workpath (Join-Path $ProjectRoot "temp_build\build") `
  --specpath (Join-Path $ProjectRoot "temp_build") `
  (Join-Path $ProjectRoot "omni_assistant.py")

$BuiltExe = Join-Path $ProjectRoot "temp_build\dist\sistem.exe"
$DestSistem = Join-Path $ProjectRoot "sistem.exe"

if (Test-Path $BuiltExe) {
  # Stop any running instances of destination executable
  Get-Process sistem -ErrorAction SilentlyContinue | Where-Object { 
    $_.Path -and ($_.Path -eq $DestSistem)
  } | Stop-Process -Force -ErrorAction SilentlyContinue
  Start-Sleep -Milliseconds 500

  if (Test-Path $DestSistem) { Remove-Item -Force $DestSistem -ErrorAction SilentlyContinue }

  Move-Item -Force $BuiltExe $DestSistem

  $SizeMB = [math]::Round((Get-Item $DestSistem).Length / 1MB, 2)
  Write-Host "SUCCESS: Built $DestSistem ($SizeMB MB)"
}

# Cleanup build artifacts and light venv
Remove-Item -Recurse -Force (Join-Path $ProjectRoot "temp_build") -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force $VenvPath -ErrorAction SilentlyContinue
