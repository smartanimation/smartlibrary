@echo off
set "SMARTPIPELINE_ROOT=%~dp0.."
set "PYTHONPATH=%SMARTPIPELINE_ROOT%\packages;%SMARTPIPELINE_ROOT%"
python -m smartlib.apps.launcher
