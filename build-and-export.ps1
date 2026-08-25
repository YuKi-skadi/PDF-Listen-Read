param(
    [string]$ImageTag = "pdf-listen-read:final",
    [string]$OutputTar = ""
)

$ErrorActionPreference = "Stop"
$ProjectDir = (Resolve-Path (Join-Path $PSScriptRoot ".")).Path
if ([string]::IsNullOrWhiteSpace($OutputTar)) {
    $OutputTar = Join-Path $ProjectDir "pdf-listen-read-final.tar"
}

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "未找到 docker 命令。请先安装并启动 Docker Desktop，或在已经安装 Docker 的 Linux/NAS 环境中运行此脚本。"
}

Write-Host "构建目录：$ProjectDir"
Write-Host "镜像标签：$ImageTag"
docker build --file (Join-Path $ProjectDir "Dockerfile") --tag $ImageTag $ProjectDir
docker save --output $OutputTar $ImageTag
Write-Host "镜像已导出：$OutputTar"
