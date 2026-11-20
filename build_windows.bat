@echo off
REM Сборка DayZ RVMAT Studio в один exe (нужен Python 3.10+ для Windows)
python -m venv .venv || goto :err
call .venv\Scripts\activate.bat
pip install -r requirements.txt pyinstaller pytest || goto :err
python -m pytest -q || goto :err
pyinstaller --noconfirm --clean --windowed --name DayZRvmatStudio --collect-submodules rvmat_studio run_studio.py || goto :err
dist\DayZRvmatStudio\DayZRvmatStudio.exe --selftest
echo.
echo Готово: dist\DayZRvmatStudio\DayZRvmatStudio.exe
exit /b 0
:err
echo СБОРКА НЕ УДАЛАСЬ
exit /b 1
