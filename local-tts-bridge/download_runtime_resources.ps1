[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [uri]$RuntimeUrl,

    [Parameter(Mandatory = $true)]
    [uri]$ModelsUrl,

    [string]$Destination = (Join-Path $PSScriptRoot 'runtime-resources'),

    [string]$RuntimeSha256 = '',

    [string]$ModelsSha256 = ''
)

$ErrorActionPreference = 'Stop'

function Assert-Sha256([string]$Path, [string]$Expected, [string]$Label) {
    if ([string]::IsNullOrWhiteSpace($Expected)) { return }
    $actual = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -ne $Expected.Trim().ToLowerInvariant()) {
        throw "$Label SHA-256 校验失败：$actual"
    }
}

function Expand-Zip([string]$Archive, [string]$OutputDirectory, [string]$Label) {
    if ([IO.Path]::GetExtension($Archive).ToLowerInvariant() -ne '.zip') {
        throw "$Label 必须是 ZIP 压缩包；其他格式请先手动解压。"
    }
    Expand-Archive -LiteralPath $Archive -DestinationPath $OutputDirectory -Force
}

$destinationPath = [IO.Path]::GetFullPath($Destination)
$downloadDirectory = Join-Path ([IO.Path]::GetTempPath()) ("pdf-tts-bridge-download-" + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $downloadDirectory -Force | Out-Null
New-Item -ItemType Directory -Path $destinationPath -Force | Out-Null

try {
    $runtimeArchive = Join-Path $downloadDirectory 'runtime.zip'
    $modelsArchive = Join-Path $downloadDirectory 'models.zip'

    Write-Host "下载运行依赖：$RuntimeUrl"
    Invoke-WebRequest -Uri $RuntimeUrl -OutFile $runtimeArchive
    Assert-Sha256 $runtimeArchive $RuntimeSha256 '运行依赖'

    Write-Host "下载模型资源：$ModelsUrl"
    Invoke-WebRequest -Uri $ModelsUrl -OutFile $modelsArchive
    Assert-Sha256 $modelsArchive $ModelsSha256 '模型资源'

    Expand-Zip $runtimeArchive $destinationPath '运行依赖'
    Expand-Zip $modelsArchive $destinationPath '模型资源'
    Write-Host "资源已准备到：$destinationPath"
    Write-Host '请打开中介软件设置，选择该目录并执行扫描。'
}
finally {
    if (Test-Path -LiteralPath $downloadDirectory) {
        Remove-Item -LiteralPath $downloadDirectory -Recurse -Force
    }
}
