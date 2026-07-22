@echo off
call "%~dp0smartpipeline_env.bat"

"%SMARTPIPELINE_PYTHON%" "%SMARTPIPELINE_ROOT%\scripts\viewer_ui.py"
