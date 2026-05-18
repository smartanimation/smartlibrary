@echo off
set "SMARTPIPELINE_ROOT=%~dp0.."
set "PYTHONPATH=%SMARTPIPELINE_ROOT%\packages;%SMARTPIPELINE_ROOT%"

set "SMARTPIPELINE_PYTHON=%SMARTPIPELINE_ROOT%\runtime\python\python.exe"
if not exist "%SMARTPIPELINE_PYTHON%" set "SMARTPIPELINE_PYTHON=python"

"%SMARTPIPELINE_PYTHON%" -m smartlib.apps.launcher
