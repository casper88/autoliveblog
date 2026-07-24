@echo off
rem Start the autoliveblog web UI at http://127.0.0.1:8766
pushd "%~dp0"
set "PY=%AUTOLIVEBLOG_PYTHON%"
if "%PY%"=="" set "PY=python"
set PYTHONIOENCODING=utf-8
start "" http://127.0.0.1:8766
"%PY%" -m uvicorn autoliveblog.web.server:app --host 127.0.0.1 --port 8766
popd
