@echo off
setlocal
if not defined SMARTPIPELINE_TOOLS set "SMARTPIPELINE_TOOLS=%~dp0..\..\..\smarttools"
set "NVIDIA_USD_ROOT=%SMARTPIPELINE_TOOLS%\usd\nvidia-25.08"
set "PYTHON=%NVIDIA_USD_ROOT%\python\python.exe"
set "LAUNCHER=%~dp0nvidia_usdpython_launcher.py"
if not exist "%PYTHON%" (
  echo NVIDIA USD Python was not found: %PYTHON%
  exit /b 1
)
if not exist "%LAUNCHER%" (
  echo NVIDIA USD Python launcher was not found: %LAUNCHER%
  exit /b 1
)
set "PATH=%NVIDIA_USD_ROOT%\lib;%NVIDIA_USD_ROOT%\bin;%NVIDIA_USD_ROOT%\plugin\usd;%NVIDIA_USD_ROOT%\python;%NVIDIA_USD_ROOT%\python\Library\bin;%NVIDIA_USD_ROOT%\pip-packages\PySide6;%NVIDIA_USD_ROOT%\pip-packages\shiboken6;%SystemRoot%\System32;%SystemRoot%;%SystemRoot%\System32\Wbem"
"%PYTHON%" "%LAUNCHER%" %*
