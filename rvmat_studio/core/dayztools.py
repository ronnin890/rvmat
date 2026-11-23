"""Запуск Workbench / поиск dayz.gproj."""
from __future__ import annotations

from pathlib import Path


class WorkbenchError(RuntimeError):
    pass


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
    if not workbench or not Path(workbench).exists():
        raise WorkbenchError("Workbench не найден: укажите путь к workbenchApp.exe в Настройках.")
    if p_drive and not Path(p_drive).exists():
        raise WorkbenchError(f"Диск {p_drive} не найден. Смонтируйте P: (DayZ Tools → Setup/Mount P drive) и повторите.")
    cands = find_gproj(gproj, p_drive, project_root, dayz_tools)
    if not cands:
        where = [x for x in (gproj, project_root, p_drive, dayz_tools) if x]
        raise WorkbenchError("Не найден файл проекта dayz.gproj.\nИскал в: " + (", ".join(where) or "(пути не заданы)")
                             + "\nУкажите его вручную в Настройках («Файл проекта dayz.gproj»), например P:\\<ваш мод>\\Workbench\\dayz.gproj.")
    gp = cands[0]
    proc = subprocess.Popen(workbench_command(workbench, gp), cwd=str(Path(workbench).parent))
    return proc, gp
