@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0" || (echo Не удалось перейти в папку программы & pause & exit /b 1)

rem --- Python (нужен 3.10+ с tkinter: обычный установщик с python.org его включает)
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY where python >nul 2>nul && set "PY=python"
if not defined PY (
  echo Python не найден. Установите Python 3.10+ с https://www.python.org/downloads/ ^(галочка "Add python.exe to PATH"^).
  pause & exit /b 1
)

rem --- первый запуск: маленькое окружение с numpy и Pillow (~40 МБ, обычно 10-20 секунд). Дальше запуск мгновенный.
if not exist ".venv\Scripts\python.exe" (
  echo Первый запуск: устанавливаю numpy и Pillow...
  %PY% -m venv .venv || goto :err
  rem зависимости ставятся по requirements.lock с проверкой хешей; если lock не подходит этой версии Python - запасной путь (без проверки хешей)
  ".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q --require-hashes -r requirements.lock || (
    echo Lock-файл не подошёл этой версии Python: устанавливаю по requirements.txt ^(без проверки хешей^).
    ".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r requirements.txt || goto :err
    ".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r requirements-gl.txt || echo Не удалось поставить moderngl: предпросмотр будет работать на процессоре.
  )
)

".venv\Scripts\python.exe" -c "import tkinter" 2>nul || (
  echo В этой установке Python нет tkinter. Переустановите Python с python.org ^(компонент "tcl/tk and IDLE"^).
  pause & exit /b 1
)

start "" ".venv\Scripts\pythonw.exe" -m rvmat_studio
exit /b 0

:err
echo Не удалось подготовить окружение. Проверьте подключение к интернету и повторите.
rmdir /s /q ".venv" 2>nul
pause
exit /b 1
