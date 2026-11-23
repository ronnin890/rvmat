@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" call start.bat
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q pytest
".venv\Scripts\python.exe" -m pytest -q
".venv\Scripts\python.exe" -m rvmat_studio --selftest
pause
