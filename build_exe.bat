@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0" || (echo Не удалось перейти в папку проекта & pause & exit /b 1)
rem Сборка автономной версии (Python пользователю не нужен). Запускать на ПК, где Python 3.10+ есть ТОЛЬКО для сборки.
rem Результат: dist\DayZRvmatStudio\DayZRvmatStudio.exe, dist\DayZRvmatStudio-portable.zip и (если установлен Inno Setup) dist\DayZRvmatStudio-Setup.exe

set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY where python >nul 2>nul && set "PY=python"
if not defined PY (echo Python не найден. Он нужен только для сборки. & pause & exit /b 1)

if not exist ".buildvenv\Scripts\python.exe" (
  %PY% -m venv .buildvenv || goto :err
)
".buildvenv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r requirements.txt pyinstaller || goto :err
".buildvenv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r requirements-gl.txt || echo moderngl не установлен: сборка будет без GPU-рендера.
".buildvenv\Scripts\python.exe" -c "import tkinter" 2>nul || (echo В этом Python нет tkinter. Переустановите Python с python.org. & pause & exit /b 1)

rmdir /s /q build dist 2>nul
".buildvenv\Scripts\python.exe" -m PyInstaller --noconfirm packaging\rvmat_studio.spec || goto :err
".buildvenv\Scripts\python.exe" -m zipfile -c dist\DayZRvmatStudio-portable.zip dist\DayZRvmatStudio || goto :err

set "ISCC="
for %%P in ("%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" "%ProgramFiles%\Inno Setup 6\ISCC.exe") do if exist %%P set "ISCC=%%~P"
if defined ISCC (
  "%ISCC%" packaging\installer.iss || goto :err
) else (
  echo Inno Setup не найден - установщик не создан ^(скачайте с https://jrsoftware.org/isdl.php^). Готова портативная версия в dist\.
)
echo Готово.
pause
exit /b 0

:err
echo Сборка не удалась.
pause
exit /b 1
