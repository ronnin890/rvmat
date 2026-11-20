"""Экспорт одного материала / комплекта / проекта."""
from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

from ..backend import Severity
from .project import Project


@dataclass
class ExportPlan:
    files: dict[Path, str]                       # путь -> текст
    existing: list[Path] = field(default_factory=list)
    issues: list = field(default_factory=list)

    @property
    def has_errors(self):
        return any(i.severity == Severity.ERROR for i in self.issues)


def plan_kit(proj: Project, out_dir) -> ExportPlan:
    out = Path(out_dir)
    files = {out / fn: m.to_text() for fn, m in proj.kit_all().items()}
    if proj.part_map:                                  # соответствие селекшенов материалам (для Object Builder)
        lines = [f"{part};{proj.slot_for_part(part).name}.rvmat" for part in sorted(proj.part_map)]
        files[out / f"{proj.title}_selections.txt"] = "# селекшен;материал\r\n" + "\r\n".join(lines) + "\r\n"
    return ExportPlan(files, [p for p in files if p.exists()], proj.validate())


def plan_single(proj: Project, state, out_dir) -> ExportPlan:
    from ..backend import kit_filename
    out = Path(out_dir)
    p = out / kit_filename(proj.name, state)
    issues = [i for i in proj.validate() if i.location.startswith(p.name + ":")]
    return ExportPlan({p: proj.effective(state).to_text()}, [p] if p.exists() else [], issues)


def execute(plan: ExportPlan, overwrite: bool = False, backup: bool = True) -> list[Path]:
    """Записывает файлы. Без overwrite=True существующие файлы не трогает (бросает FileExistsError)."""
    if plan.existing and not overwrite:
        raise FileExistsError("; ".join(str(p) for p in plan.existing))
    written = []
    for p, text in plan.files.items():
        p.parent.mkdir(parents=True, exist_ok=True)
        if p.exists() and backup:
            shutil.copy2(p, p.with_name(p.name + ".bak"))
        p.write_bytes(text.encode("utf-8"))   # CRLF внутри текста, без BOM
        written.append(p)
    return written


def export_project(proj: Project, out_dir, copy_textures=True, overwrite=False) -> list[Path]:
    """Комплект + .rvproj + копии исходных текстур библиотеки (папка textures/)."""
    out = Path(out_dir)
    plan = plan_kit(proj, out)
    written = execute(plan, overwrite)
    proj_file = out / f"{proj.title}.rvproj"
    if proj_file.exists() and not overwrite:
        raise FileExistsError(str(proj_file))
    proj.save(proj_file) if proj.path is None else proj.save(proj.path)
    if proj.path != proj_file:
        shutil.copy2(proj.path, proj_file)
    written.append(proj_file)
    if copy_textures:
        for t in proj.textures:
            src = Path(t)
            if src.exists():
                dst = out / "textures" / src.name
                dst.parent.mkdir(exist_ok=True)
                shutil.copy2(src, dst)
                written.append(dst)
    return written


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
