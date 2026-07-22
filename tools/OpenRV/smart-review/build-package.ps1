param(
    [string]$OutDir = "P:\dev\smartlibrary\tools\openrv\dist"
)

$ErrorActionPreference = "Stop"
$source = Split-Path -Parent $MyInvocation.MyCommand.Path
$packageName = "smart_review-0.1.rvpkg"
$out = Join-Path $OutDir $packageName
$zipOut = Join-Path $OutDir "smart_review-0.1.zip"

New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
if (Test-Path $out) {
    Remove-Item -LiteralPath $out -Force
}
if (Test-Path $zipOut) {
    Remove-Item -LiteralPath $zipOut -Force
}

$tmp = Join-Path $env:TEMP ("smart_review_rvpkg_" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Force -Path $tmp | Out-Null
Copy-Item -LiteralPath (Join-Path $source "PACKAGE") -Destination $tmp
Copy-Item -LiteralPath (Join-Path $source "smart_review.py") -Destination $tmp

Compress-Archive -Path (Join-Path $tmp "*") -DestinationPath $zipOut -Force
Move-Item -LiteralPath $zipOut -Destination $out -Force
Remove-Item -LiteralPath $tmp -Recurse -Force

Write-Host "Built $out"
