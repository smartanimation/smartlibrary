@echo off
set "SMARTPIPELINE_ROOT=%~dp0.."
set "SMARTLIBRARY_ROOT=%SMARTPIPELINE_ROOT%"
set "PROJECT_CONFIG_DIR=%SMARTPIPELINE_ROOT%\config\STKB"
set "PYTHONPATH=%SMARTPIPELINE_ROOT%\packages;%SMARTPIPELINE_ROOT%"

set "SMARTPIPELINE_PYTHON=%SMARTPIPELINE_ROOT%\runtime\python\Scripts\python.exe"
if not exist "%SMARTPIPELINE_PYTHON%" set "SMARTPIPELINE_PYTHON=python"

if "%~1"=="" goto ui

"%SMARTPIPELINE_PYTHON%" "%SMARTPIPELINE_ROOT%\scripts\editorial_intake.py" --config "%PROJECT_CONFIG_DIR%" %*
pause
exit /b %ERRORLEVEL%

:ui
"%SMARTPIPELINE_PYTHON%" -m smartlib.apps.editorial_intake
pause
exit /b %ERRORLEVEL%

:usage
echo Usage:
echo   %~nx0 template D:\Projects\STKB\incoming\editorial\events_template.csv
echo   %~nx0 intake --csv D:\Projects\STKB\incoming\editorial\events.csv --mov D:\Projects\STKB\incoming\editorial\offline.mov --comment "first editorial publish"
pause
exit /b 1
