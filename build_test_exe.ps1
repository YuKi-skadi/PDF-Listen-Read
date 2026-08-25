$ErrorActionPreference = 'Stop'

$sourceDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$workspaceDir = Split-Path -Parent $sourceDir
$testDir = Join-Path $workspaceDir 'PDF-Listen-Read-v1'
$python = Join-Path $testDir '.venv\Scripts\python.exe'
$outputDir = Join-Path $testDir 'dist-rebuild'
$outputProjectDir = Join-Path $outputDir 'PDF-Listen-Read-rebuild'
$dataDir = Join-Path $outputProjectDir 'data'
$backupDir = Join-Path $testDir 'data-rebuild-backup'

if (-not (Test-Path -LiteralPath $python)) {
    throw "Test build environment not found: $python"
}

Set-Location $sourceDir
if (Test-Path -LiteralPath $dataDir) {
    New-Item -ItemType Directory -Force -Path $backupDir | Out-Null
    Copy-Item -Path (Join-Path $dataDir '*') -Destination $backupDir -Recurse -Force
    Write-Host "Existing test data backed up to: $backupDir"
}

& $python -m PyInstaller `
    --noconfirm `
    --clean `
    --name PDF-Listen-Read-rebuild `
    --onedir `
    --console `
    --distpath $outputDir `
    --workpath (Join-Path $sourceDir 'build-exe') `
    --specpath $sourceDir `
    --add-data 'app\static;app\static' `
    --hidden-import=edge_tts `
    --hidden-import=dashscope `
    --hidden-import=pydub `
    --collect-all imageio_ffmpeg `
    launcher.py

if (Test-Path -LiteralPath $backupDir) {
    New-Item -ItemType Directory -Force -Path $dataDir | Out-Null
    Copy-Item -Path (Join-Path $backupDir '*') -Destination $dataDir -Recurse -Force
    Write-Host "Test data restored to: $dataDir"
}

Write-Host "Build complete: $outputDir\PDF-Listen-Read-rebuild\PDF-Listen-Read-rebuild.exe"
