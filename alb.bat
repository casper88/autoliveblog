@echo off
rem autoliveblog launcher: alb.bat <url> [options]
rem Set AUTOLIVEBLOG_PYTHON env var if "python" is not your Python 3.11+ interpreter.
pushd "%~dp0"
set "PY=%AUTOLIVEBLOG_PYTHON%"
if "%PY%"=="" set "PY=python"
"%PY%" -m autoliveblog %*
popd
