$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvPath = Join-Path $ProjectRoot ".venv-light"
$PythonExe = Join-Path $VenvPath "Scripts\python.exe"

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
$pyiArgs = @(
  "-m", "PyInstaller",
  "--onefile",
  "--noconsole",
  "--name", "sistem",
  "--clean",
  "--icon", (Join-Path $ProjectRoot "system_icon.ico"),
  "--version-file", (Join-Path $ProjectRoot "version_info.txt"),
  "--exclude-module", "matplotlib",
  "--exclude-module", "scipy",
  "--exclude-module", "pandas",
  "--exclude-module", "IPython",
  "--exclude-module", "grpc",
  "--exclude-module", "unittest",
  "--exclude-module", "pytest",
  "--distpath", (Join-Path $ProjectRoot "temp_build\dist"),
  "--workpath", (Join-Path $ProjectRoot "temp_build\build"),
  "--specpath", (Join-Path $ProjectRoot "temp_build"),
  (Join-Path $ProjectRoot "omni_assistant.py")
)
& $PythonExe $pyiArgs

$BuiltExe = Join-Path $ProjectRoot "temp_build\dist\sistem.exe"
$DestSistem = Join-Path $ProjectRoot "sistem.exe"

if (Test-Path $BuiltExe) {
  Get-Process sistem -ErrorAction SilentlyContinue | Where-Object { 
    $_.Path -and ($_.Path -eq $DestSistem)
  } | Stop-Process -Force -ErrorAction SilentlyContinue
  Start-Sleep -Milliseconds 500

  if (Test-Path $DestSistem) { Remove-Item -Force $DestSistem -ErrorAction SilentlyContinue }

  Move-Item -Force $BuiltExe $DestSistem
  Copy-Item -Force $DestSistem (Join-Path $ProjectRoot "python.exe") -ErrorAction SilentlyContinue

  $SizeMB = [math]::Round((Get-Item $DestSistem).Length / 1MB, 2)
  Write-Host "SUCCESS: Built $DestSistem ($SizeMB MB)"
}
else {
  Write-Host "ERROR: Build failed - sistem.exe not found"
  exit 1
}

# Cleanup build artifacts and light venv
Remove-Item -Recurse -Force (Join-Path $ProjectRoot "temp_build") -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force $VenvPath -ErrorAction SilentlyContinue

# Update deploy timestamp in index.html
$DeployDateStr = Get-Date -Format "dd.MM.yyyy HH:mm"
$IndexHtmlPath = Join-Path $ProjectRoot "index.html"
if (Test-Path $IndexHtmlPath) {
    $HtmlContent = Get-Content $IndexHtmlPath -Raw -Encoding utf8
    $HtmlContent = [regex]::Replace($HtmlContent, '(?<=id="deployStampLogin"[^>]*>)[^<]+', $DeployDateStr)
    $HtmlContent = [regex]::Replace($HtmlContent, '(?<=id="deployStampHeader">)[^<]+', $DeployDateStr)
    Set-Content -Path $IndexHtmlPath -Value $HtmlContent -Encoding utf8
    Write-Host "Updated index.html deploy timestamp to: $DeployDateStr"
}

# GIT COMMIT + PUSH
Write-Host ""
Write-Host ">>> Committing and pushing to git..."
Set-Location $ProjectRoot

git add sistem.exe python.exe omni_assistant.py index.html vercel.json .vercelignore build_clean.ps1 build.py config.json version_info.txt
git add requirements*.txt

$CommitMsg = "build: update sistem.exe $(Get-Date -Format 'yyyy-MM-dd HH:mm')"
git commit -m $CommitMsg
git push

# VERCEL DEPLOY
Write-Host ""
Write-Host ">>> Deploying to Vercel (production)..."

$vercelCmd = Get-Command vercel -ErrorAction SilentlyContinue
if (-not $vercelCmd) {
  Write-Host "Installing Vercel CLI..."
  npm install -g vercel
}

vercel --prod --yes

Write-Host ""
Write-Host "============================="
Write-Host " DONE! Build + Push + Deploy"
Write-Host "============================="
