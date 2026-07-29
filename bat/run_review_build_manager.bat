@echo off
call "%~dp0smartpipeline_env.bat"

"%SMARTPIPELINE_PYTHON%" -m smartlib.apps.review_build_manager
pause
