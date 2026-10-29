# 下载 BtbN 的 LGPL 版 ffmpeg（Windows x64）到 tools/ffmpeg
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$zip = Join-Path $env:TEMP "ffmpeg-lgpl.zip"
$url = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-lgpl.zip"
Write-Host "下载 $url"
Invoke-WebRequest -Uri $url -OutFile $zip
$target = Join-Path $root "tools"
Expand-Archive -Path $zip -DestinationPath $target -Force
$inner = Get-ChildItem $target -Directory | Where-Object { $_.Name -like "ffmpeg-master-*" } | Select-Object -First 1
if ($inner) { Move-Item $inner.FullName (Join-Path $target "ffmpeg") -Force }
Remove-Item $zip -Force
Write-Host "完成：tools/ffmpeg/bin/ffmpeg.exe"
