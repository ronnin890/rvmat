"""Запуск Workbench / поиск dayz.gproj."""
from __future__ import annotations

import os
from pathlib import Path


class ToolError(RuntimeError):
    """Внешняя программа не прошла проверку или запрещена."""


class WorkbenchError(ToolError):
    pass


EXPECTED_NAMES = {"image_to_paa": "imagetopaa.exe", "workbench": "workbenchapp.exe"}

_trusted: set[str] = set()      # пути exe, которые пользователь явно подтвердил (хранится в настройках)
enforce_trust = False           # приложение включает; в тестах/CLI выключено


def _norm(p) -> str:
    try:
        return os.path.normcase(str(Path(p).resolve()))
    except (OSError, RuntimeError):
        return os.path.normcase(os.path.abspath(str(p)))


def set_trusted(paths) -> None:
    _trusted.clear()
    _trusted.update(_norm(p) for p in paths or [])


def trust(path) -> None:
    _trusted.add(_norm(path))


def is_trusted(path) -> bool:
    return _norm(path) in _trusted


def validate_exe(path: str, kind: str, check_trust: bool = True) -> Path:
    """Файл существует, это файл, и имя совпадает с ожидаемым (регистр не важен). Иначе ToolError."""
    want = EXPECTED_NAMES[kind]
    if not path or "\x00" in str(path):
        raise ToolError("Путь к программе не задан (Настройки).")
    p = Path(path)
    if not p.is_file():
        raise ToolError(f"Не найден файл программы: {path}")
    if p.name.lower() != want:
        raise ToolError(f"Ожидался файл {want}, указан {p.name} — запуск отклонён.")
    if check_trust and enforce_trust and not is_trusted(p):
        raise ToolError(f"Программа {p} ещё не подтверждена пользователем (подтвердите запуск в диалоге).")
    return p


def find_gproj(explicit: str = "", p_drive: str = "", project_root: str = "", dayz_tools: str = "") -> list[Path]:
    """Кандидаты dayz.gproj (по убыванию приоритета): указанный явно, корень P:/проекта,
    папки Workbench внутри них (глубина ≤ 3), каталог Workbench в DayZ Tools."""
    found: list[Path] = []
    add = lambda p: found.append(p) if p.is_file() and p not in found else None
    if explicit:
        add(Path(explicit))
    for root in (project_root, p_drive):
        if not root or not Path(root).is_dir():
            continue
        r = Path(root)
        add(r / "dayz.gproj")
        for pat in ("*/dayz.gproj", "*/Workbench/dayz.gproj", "*/*/Workbench/dayz.gproj", "Workbench/dayz.gproj"):
            try:
                for p in r.glob(pat):
                    add(p)
            except OSError:
                pass
    if dayz_tools:
        for sub in ("Bin/Workbench", "Workbench"):
            add(Path(dayz_tools) / sub / "dayz.gproj")
    return found


def workbench_command(workbench: str, gproj: Path) -> list[str]:
    """Командная строка запуска: Workbench с явным проектом (-project="...dayz.gproj")."""
    return [str(workbench), f"-project={gproj}"]


def check_in_workbench(workbench: str, gproj: str = "", p_drive: str = "", project_root: str = "", dayz_tools: str = ""):
    """Запускает Workbench с найденным dayz.gproj и рабочей папкой = папка Workbench.
    Файл .rvmat напрямую не открывается: его нужно найти в Resource Browser по игровому пути.
    Не проверено на реальных DayZ Tools (флаг -project взят из руководств сообщества)."""
    import subprocess
    try:
        wb = validate_exe(workbench, "workbench")
    except ToolError as e:
        raise WorkbenchError(f"{e}\nУкажите путь к workbenchApp.exe в Настройках.") from e
    if p_drive and not Path(p_drive).exists():
        raise WorkbenchError(f"Диск {p_drive} не найден. Смонтируйте P: (DayZ Tools → Setup/Mount P drive) и повторите.")
    cands = find_gproj(gproj, p_drive, project_root, dayz_tools)
    if not cands:
        where = [x for x in (gproj, project_root, p_drive, dayz_tools) if x]
        raise WorkbenchError("Не найден файл проекта dayz.gproj.\nИскал в: " + (", ".join(where) or "(пути не заданы)")
                             + "\nУкажите его вручную в Настройках («Файл проекта dayz.gproj»), например P:\\<ваш мод>\\Workbench\\dayz.gproj.")
    gp = cands[0]
    # GUI-программа живёт неограниченно долго, поэтому здесь Popen без timeout (список аргументов, без shell)
    proc = subprocess.Popen(workbench_command(str(wb), gp), cwd=str(wb.parent))
    return proc, gp


def open_folder(path) -> bool:
    """Открыть папку в проводнике. Только существующая папка; путь абсолютный и (где поддерживается) после «--»,
    чтобы имя, начинающееся с «-», не воспринималось как ключ. Всегда список аргументов, без shell, с timeout."""
    import subprocess
    import sys
    try:
        p = Path(path).resolve()
        if not p.is_dir():
            return False
        if sys.platform == "win32":
            os.startfile(str(p))                                              # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.run(["open", "--", str(p)], timeout=15, shell=False, check=False)
        else:
            subprocess.run(["xdg-open", str(p)], timeout=15, shell=False, check=False)      # абсолютный путь не может начинаться с «-»
        return True
    except (OSError, subprocess.SubprocessError, RuntimeError):
        return False
