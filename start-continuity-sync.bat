@echo off
setlocal
cd /d "%~dp0"
python scripts\sync_agent.py serve --open
pause
