@echo off
setlocal
if not defined SMARTPIPELINE_TOOLS set "SMARTPIPELINE_TOOLS=%~dp0..\..\..\smarttools"
set "NVIDIA_USD_ROOT=%SMARTPIPELINE_TOOLS%\usd\nvidia-25.08"
set "NVIDIA_PYTHON=%NVIDIA_USD_ROOT%\python\python.exe"
set "NVIDIA_USDVIEW_LAUNCHER=%~dp0nvidia_usdview_launcher.py"
if exist "%NVIDIA_PYTHON%" if exist "%NVIDIA_USDVIEW_LAUNCHER%" (
  set "NVIDIA_USD_ROOT=%NVIDIA_USD_ROOT%"
  set "PATH=%NVIDIA_USD_ROOT%\lib;%NVIDIA_USD_ROOT%\bin;%NVIDIA_USD_ROOT%\plugin\usd;%NVIDIA_USD_ROOT%\python;%NVIDIA_USD_ROOT%\python\Library\bin;%NVIDIA_USD_ROOT%\pip-packages\PySide6;%NVIDIA_USD_ROOT%\pip-packages\shiboken6;%SystemRoot%\System32;%SystemRoot%;%SystemRoot%\System32\Wbem"
  "%NVIDIA_PYTHON%" "%NVIDIA_USDVIEW_LAUNCHER%" %*
  exit /b %ERRORLEVEL%
)
set "HOUDINI_ROOT=C:\Program Files\Side Effects Software\Houdini 21.0.440"
if defined HFS set "HOUDINI_ROOT=%HFS%"
set "HFS=%HOUDINI_ROOT%"
set "PXR_USDVIEW_SUPPRESS_STATE_SAVING=1"
set "HYTHON=%HOUDINI_ROOT%\bin\hython.exe"
set "USDVIEW_LAUNCHER=%~dp0usdview_launcher.py"
if not exist "%HYTHON%" (
  echo hython.exe was not found: %HYTHON%
  exit /b 1
)
if not exist "%USDVIEW_LAUNCHER%" (
  echo usdview launcher was not found: %USDVIEW_LAUNCHER%
  exit /b 1
)
set "PATH=%HOUDINI_ROOT%\bin;%HOUDINI_ROOT%\dsolib;%SystemRoot%\System32;%SystemRoot%;%SystemRoot%\System32\Wbem"
"%HYTHON%" "%USDVIEW_LAUNCHER%" %*
