param (
    [string]$mxlFilePath,
    [string]$outputDir
)

# 检查参数是否为空
if (-not $mxlFilePath) {
    Write-Error "Error: mxlFilePath parameter is missing or empty."
    exit 1
}

if (-not $outputDir) {
    Write-Error "Error: outputDir parameter is missing or empty."
    exit 1
}

# 检查输入文件是否存在
if (-not (Test-Path $mxlFilePath)) {
    Write-Host "错误：文件不存在！路径：$mxlFilePath" -ForegroundColor Red
    exit 1
}

# 确保输出目录存在
New-Item -ItemType Directory -Path $outputDir -Force | Out-Null

try {
    # 显式加载必要的程序集
    Add-Type -AssemblyName System.IO.Compression
    Add-Type -AssemblyName System.IO.Compression.FileSystem

    # 打开 ZIP 文件（使用 UTF-8 编码）
    $archive = [System.IO.Compression.ZipFile]::Open(
        $mxlFilePath,
        [System.IO.Compression.ZipArchiveMode]::Read,
        [System.Text.Encoding]::UTF8
    )

    foreach ($entry in $archive.Entries) {
        $destPath = Join-Path $outputDir $entry.FullName
        $destDir = [System.IO.Path]::GetDirectoryName($destPath)
        if (-not (Test-Path $destDir)) {
            New-Item -ItemType Directory -Path $destDir -Force | Out-Null
        }
        [System.IO.Compression.ZipFileExtensions]::ExtractToFile(
            $entry,
            $destPath,
            $true
        )
    }

    $archive.Dispose()
    Write-Host "解压成功！文件已保存到：$outputDir" -ForegroundColor Green
} catch {
    # 修正字符串终止符和编码问题
    Write-Host "解压失败！错误详情：$_" -ForegroundColor Red
    exit 1
}