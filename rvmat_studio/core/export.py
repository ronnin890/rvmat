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
    files = {out / fn: m.to_text() for fn, m in proj.kit().items()}
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
    proj_file = out / f"{proj.name}.rvproj"
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


def check_in_workbench(workbench: str, rvmat: Path):
    """Открыть материал в Workbench (если путь задан). Не проверено в этой сборке — требует DayZ Tools."""
    import subprocess
    if not workbench or not Path(workbench).exists():
        raise FileNotFoundError("Workbench не найден; укажите путь в настройках")
    return subprocess.Popen([workbench, str(rvmat)])
