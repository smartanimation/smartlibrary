param(
    [string]$SupportRoot = "",
    [string]$RepoRoot = "P:\dev\smartlibrary"
)

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$dist = Join-Path (Split-Path -Parent $scriptDir) "dist"
& (Join-Path $scriptDir "build-package.ps1") -OutDir $dist

if ($SupportRoot) {
    $supportRoots = @($SupportRoot)
} else {
    $supportRoots = @(
        "$env:APPDATA\RV",
        "$env:APPDATA\TweakSoftware\RV"
    ) | Select-Object -Unique
}

$entry = Get-Content -LiteralPath (Join-Path $scriptDir "rvload2") | Where-Object { $_ -and $_ -notmatch '^\d+$' } | Select-Object -First 1
foreach ($root in $supportRoots) {
    $packages = Join-Path $root "Packages"
    $mu = Join-Path $root "Mu"
    $python = Join-Path $root "Python"
    New-Item -ItemType Directory -Force -Path $packages | Out-Null
    New-Item -ItemType Directory -Force -Path $mu | Out-Null
    New-Item -ItemType Directory -Force -Path $python | Out-Null
    Copy-Item -LiteralPath (Join-Path $dist "smart_review-0.1.rvpkg") -Destination $packages -Force
    Copy-Item -LiteralPath (Join-Path $scriptDir "smart_review.py") -Destination $python -Force

    $rvload2 = Join-Path $mu "rvload2"
    if (Test-Path $rvload2) {
        $existing = Get-Content -LiteralPath $rvload2
        Copy-Item -LiteralPath $rvload2 -Destination "$rvload2.bak" -Force
    } else {
        $existing = @("4")
    }
    if (-not $existing -or $existing[0] -ne "4") {
        $existing = @("4") + ($existing | Where-Object { $_ -and $_ -notmatch '^\d+$' })
    }
    $next = @($existing[0])
    if ($existing.Count -gt 1) {
        $next += $existing[1..($existing.Count - 1)] | Where-Object { $_ -and ($_ -notmatch '^smart_review,') }
    }
    $next += $entry
    Set-Content -LiteralPath $rvload2 -Value $next -Encoding ASCII

    Write-Host "Installed Smart Review RV package:"
    Write-Host "  Package: $packages\smart_review-0.1.rvpkg"
    Write-Host "  Python:  $python\smart_review.py"
    Write-Host "  Loader:  $rvload2"

    $legacyRvload2 = Join-Path $root "rvload2"
    if (Test-Path $legacyRvload2) {
        $legacy = Get-Content -LiteralPath $legacyRvload2
        $cleanLegacy = $legacy | Where-Object { $_ -and ($_ -notmatch '^smart_review,') }
        if (($cleanLegacy -join "`n") -ne ($legacy -join "`n")) {
            Copy-Item -LiteralPath $legacyRvload2 -Destination "$legacyRvload2.bak" -Force
            Set-Content -LiteralPath $legacyRvload2 -Value $cleanLegacy -Encoding ASCII
            Write-Host "  Cleaned legacy loader: $legacyRvload2"
        }
    }
}

Write-Host ""
Write-Host "Set SMARTLIBRARY_ROOT for RV if needed:"
Write-Host "  setx SMARTLIBRARY_ROOT `"$RepoRoot`""
