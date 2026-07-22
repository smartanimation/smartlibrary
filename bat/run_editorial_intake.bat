@echo off
call "%~dp0smartpipeline_env.bat"

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
