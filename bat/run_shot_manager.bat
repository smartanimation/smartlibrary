@echo off
call "%~dp0smartpipeline_env.bat"

"%SMARTPIPELINE_PYTHON%" "%SMARTPIPELINE_ROOT%\scripts\shot_manager_ui.py"
pause
