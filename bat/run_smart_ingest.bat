@echo off
setlocal

call "%~dp0smartpipeline_env.bat"

"%SMARTPIPELINE_PYTHON%" -m smartlib.apps.smart_ingest %*
