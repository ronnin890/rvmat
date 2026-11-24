@echo off
rem Only ASCII in this file on purpose (cmd.exe mishandles UTF-8 inside blocks and labels). Messages in Russian: scripts\bootstrap.py
chcp 65001 >nul
setlocal
set "PYTHONUTF8=1"
cd /d "%~dp0" || goto :nocd

set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY where python >nul 2>nul && set "PY=python"
if not defined PY goto :nopython

if exist ".venv\Scripts\python.exe" goto :haveenv
echo Creating environment .venv ...
%PY% -m venv .venv
if errorlevel 1 goto :venvfail

:haveenv
".venv\Scripts\python.exe" scripts\bootstrap.py
if errorlevel 1 goto :fail
exit /b 0

:nocd
echo Cannot open the program folder.
pause
exit /b 1

:nopython
echo Python was not found. Install Python 3.10+ from https://www.python.org/downloads/ and tick "Add python.exe to PATH".
echo Python ne najden. Ustanovite Python 3.10+ s python.org.
pause
exit /b 1

:venvfail
echo Could not create .venv. Removing the incomplete folder; run start.bat again.
rmdir /s /q ".venv" 2>nul
pause
exit /b 1

:fail
echo.
echo Start failed - see the messages above.
pause
exit /b 1
