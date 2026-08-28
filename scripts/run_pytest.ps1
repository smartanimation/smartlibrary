[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]] $PytestArgs
)

$ErrorActionPreference = "Stop"
$repositoryRoot = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $repositoryRoot ".venv\Scripts\python.exe"
$python = if ($env:SMARTLIBRARY_TEST_PYTHON) { $env:SMARTLIBRARY_TEST_PYTHON } else { $venvPython }

Write-Host "[1/3] shell: ok"
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Test Python was not found: $python. Create .venv and install -e `".[dev]`"."
}
Write-Host "[2/3] python: $python"
& $python -c "import sys; print(sys.version); print(sys.executable)"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $python -c "import pytest; print('pytest ' + pytest.__version__)"
if ($LASTEXITCODE -ne 0) { throw "pytest is unavailable; install the dev dependencies." }

# Keep task-owned pytest directories out of the workspace: Windows sandbox
# identities can differ between Codex tasks and break a later setup refresh.
$tempRoot = [System.IO.Path]::GetTempPath()
$baseTemp = Join-Path $tempRoot ("smartlibrary-pytest-{0}-{1}" -f $PID, [guid]::NewGuid().ToString("N"))
Write-Host "[3/3] pytest: temporary files at $baseTemp"
try {
    & $python -m pytest --basetemp=$baseTemp @PytestArgs
    exit $LASTEXITCODE
} finally {
    if (Test-Path -LiteralPath $baseTemp) {
        Remove-Item -LiteralPath $baseTemp -Recurse -Force -ErrorAction SilentlyContinue
    }
}
