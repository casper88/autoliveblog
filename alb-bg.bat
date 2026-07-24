@echo off
rem Watch a live stream in a minimized background window.
rem To stop: open the window and press Ctrl+C (a final summary will be produced).
start "autoliveblog live" /min cmd /k ""%~dp0alb.bat" %*"
echo Watching in background window. Live summary: summaries\live_*.md
