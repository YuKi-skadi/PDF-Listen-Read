param(
    [string]$RuntimePython = "",
    [string]$OutputDir = ""
)

$ErrorActionPreference = "Stop"
$SourceDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$WorkspaceDir = Split-Path -Parent $SourceDir
$PackageDir = if ($OutputDir) {
    if ([System.IO.Path]::IsPathRooted($OutputDir)) { [System.IO.Path]::GetFullPath($OutputDir) }
    else { [System.IO.Path]::GetFullPath((Join-Path $SourceDir $OutputDir)) }
} else { Join-Path $WorkspaceDir "pdf-local-tts-bridge-package" }
$BuildDir = Join-Path $SourceDir ".build"
$ExistingDataDir = Join-Path $PackageDir "PdfLocalTTSBridge\data"
$PreservedDataDir = Join-Path $BuildDir "preserved-data"
Set-Location $SourceDir

if ([string]::IsNullOrWhiteSpace($RuntimePython) -or -not (Test-Path -LiteralPath $RuntimePython)) {
    throw "找不到用于打包的 Python：$RuntimePython。请把 -RuntimePython 指向已经装好 PySide6 和 PyInstaller 的 Python。"
}

New-Item -ItemType Directory -Force -Path $PackageDir | Out-Null
if (Test-Path -LiteralPath $ExistingDataDir) {
    if (Test-Path -LiteralPath $PreservedDataDir) {
        Remove-Item -LiteralPath $PreservedDataDir -Recurse -Force
    }
    Copy-Item -LiteralPath $ExistingDataDir -Destination $PreservedDataDir -Recurse -Force
}

& $RuntimePython -m PyInstaller --noconfirm --clean --windowed --onedir `
    --name PdfLocalTTSBridge `
    --distpath $PackageDir `
    --workpath $BuildDir `
    --specpath $BuildDir `
    --collect-all PySide6 `
    --hidden-import bridge_server `
    --hidden-import job_manager `
    --hidden-import runtime_manager `
    --hidden-import audio_package `
    main.py

Copy-Item -LiteralPath (Join-Path $SourceDir "README.md") -Destination (Join-Path $PackageDir "README.md") -Force
if (Test-Path -LiteralPath $PreservedDataDir) {
    $NewDataDir = Join-Path $PackageDir "PdfLocalTTSBridge\data"
    New-Item -ItemType Directory -Force -Path $NewDataDir | Out-Null
    Copy-Item -Path (Join-Path $PreservedDataDir "*") -Destination $NewDataDir -Recurse -Force
}
Write-Host "源代码目录：$SourceDir"
Write-Host "封装软件目录：$PackageDir\PdfLocalTTSBridge"
Write-Host "TTS 推理运行时和模型不会被打进 EXE；启动后请在设置页选择目标电脑上的外部资源根目录。"
