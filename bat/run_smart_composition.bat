@echo off
setlocal
call "%~dp0smartpipeline_env.bat"
call "%SMARTPIPELINE_ROOT%\tools\usd\usdpython.bat" -m smartlib.apps.smart_composition.main %*
if errorlevel 1 pause
