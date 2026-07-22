@echo off
setlocal
if not defined SMARTPIPELINE_TOOLS set "SMARTPIPELINE_TOOLS=%~dp0..\..\..\smarttools"
set "NVIDIA_USD_ROOT=%SMARTPIPELINE_TOOLS%\usd\nvidia-25.08"
set "NVIDIA_USDCAT=%NVIDIA_USD_ROOT%\scripts\usdcat.bat"
if exist "%NVIDIA_USDCAT%" (
  call "%NVIDIA_USDCAT%" %*
  exit /b %ERRORLEVEL%
)
set "HOUDINI_ROOT=C:\Program Files\Side Effects Software\Houdini 21.0.440"
if defined HFS set "HOUDINI_ROOT=%HFS%"
set "HFS=%HOUDINI_ROOT%"
set "USDCAT=%HOUDINI_ROOT%\bin\usdcat.exe"
if not exist "%USDCAT%" (
  echo usdcat.exe was not found: %USDCAT%
  exit /b 1
)
set "PATH=%HOUDINI_ROOT%\bin;%PATH%"
"%USDCAT%" %*
