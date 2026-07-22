param(
    [string[]]$CepVersion = @("11", "12"),
    [string]$ExtensionName = "smart-ae-browser"
)

$ErrorActionPreference = "Stop"

$Source = Split-Path -Parent $MyInvocation.MyCommand.Path
$Destination = Join-Path $env:APPDATA "Adobe\CEP\extensions\$ExtensionName"

New-Item -ItemType Directory -Force -Path $Destination | Out-Null
Copy-Item -Path (Join-Path $Source "*") -Destination $Destination -Recurse -Force

foreach ($Version in $CepVersion) {
    $CsxsKey = "HKCU:\Software\Adobe\CSXS.$Version"
    New-Item -Path $CsxsKey -Force | Out-Null
    New-ItemProperty -Path $CsxsKey -Name "PlayerDebugMode" -Value "1" -PropertyType String -Force | Out-Null
}

Write-Host "Installed smart AE browser to $Destination"
Write-Host "Restart After Effects, then open Window > Extensions > smart AE browser."
