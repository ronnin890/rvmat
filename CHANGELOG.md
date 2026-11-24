# История версий

Версии выкладываются в ветку `release` с тегом `vX.Y.Z` (см. [docs/RELEASING.md](docs/RELEASING.md)).

## 0.3.0
- Запуск: `start.bat` переписан (только ASCII, без блоков со скобками), установка зависимостей видна в консоли, ошибка при старте показывается и сохраняется в файл (`%TEMP%\rvmat_studio_start.log`, `%APPDATA%\DayZRvmatStudio\logs\startup_error.log`).
- Автономная сборка без Python: папка/zip и установщик (Inno Setup), `build_exe.bat`, workflow `release`.
- Безопасность (docs/TZ_SECURITY.md): лимиты и защита парсеров FBX/PAA/OBJ/RVMAT/JSON, проверка путей и внешних ссылок проекта, подтверждение запуска внешних программ, проверка схем, атомарная запись, журнал.
- CI: permissions, PR, матрица Python, CodeQL, Dependabot, lock-файл с хешами, фаззинг-тесты.

## 0.2.0
- Автозагрузка папки проекта `<меш>/(<меш>.fbx, data/)`; экспорт «как в Blender»; GPU-предпросмотр (moderngl) с CPU-запасным вариантом; приложение на tkinter без Qt.

## 0.1.0
- Первая версия: редактор RVMAT, комплект из 6 состояний, валидатор, пресеты.
