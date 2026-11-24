"""Диагностика RVMAT: критические ошибки / предупреждения / информация."""
from __future__ import annotations

import enum
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .kit import ALL_STATES, State, default_stage3, CONDITION_STAGE
from .model import REQUIRED_PARAMS, COLOR_PARAMS, UV_KEYS, Material
from .procedural import check_procedural, is_procedural

EXPECTED_SUPER_STAGES = 7   # по образцам; другие количества — лишь предупреждение


class Severity(enum.IntEnum):
    INFO = 0
    WARNING = 1
    ERROR = 2        # критическая


@dataclass
class Issue:
    severity: Severity
    code: str
    message: str
    location: str = ""      # путь к параметру для перехода в UI: "Stage3.texture"

    def __str__(self):
        return f"[{self.severity.name}] {self.code} {self.location}: {self.message}"


def normalize_game_path(tex: str) -> str:
    return tex.replace("/", "\\").lstrip("\\").lower()


SOURCE_EXTS = (".png", ".tga", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff")


def resolve_physical(tex: str, project_root: Path, allow_source: bool = False) -> Optional[Path]:
    """Игровой путь -> физический путь под корнем проекта/P:. Регистр игнорируется.
    allow_source: вместо .paa допускается исходник (png/tga/…) с тем же именем."""
    cur = project_root
    parts = tex.replace("/", "\\").strip("\\").split("\\")
    for i, part in enumerate(parts):
        if not cur.is_dir():
            return None
        hit = next((c for c in cur.iterdir() if c.name.lower() == part.lower()), None)
        if hit is None and allow_source and i == len(parts) - 1:
            stem = Path(part).stem.lower()
            hit = next((c for c in cur.iterdir() if c.stem.lower() == stem and c.suffix.lower() in SOURCE_EXTS), None)
        if hit is None:
            return None
        cur = hit
    return cur


def validate(m: Material, project_root: Optional[Path] = None,
             expected_state: Optional[State] = None) -> list[Issue]:
    out: list[Issue] = []
    add = lambda sev, code, msg, loc="": out.append(Issue(sev, code, msg, loc))

    for name in REQUIRED_PARAMS:
        p = m.root.get_prop(name)
        if p is None:
            add(Severity.ERROR, "E100", f"Отсутствует обязательный параметр {name}", name)
        elif name in COLOR_PARAMS and (p.value.kind != "array" or len(p.value.items) != 4):
            add(Severity.ERROR, "E101", f"{name} должен быть массивом RGBA из 4 чисел", name)
    for n in m.root.props():
        if n.name.lower() == "emissive":
            add(Severity.WARNING, "W101", "В RVMAT DayZ параметр пишется 'emmisive' (с двумя m)", n.name)

    stages = m.stages()
    if not stages:
        add(Severity.ERROR, "E200", "Нет ни одной текстурной стадии")
    idx = [s.index for s in stages]
    if idx and idx != list(range(1, len(idx) + 1)):
        add(Severity.ERROR, "E201", f"Стадии должны идти подряд с Stage1 (найдено {idx})", "Stage1")
    ps = m.get("PixelShaderID")
    if ps == "Super" and len(stages) != EXPECTED_SUPER_STAGES:
        add(Severity.WARNING, "W200",
            f"Для шейдера Super в образцах {EXPECTED_SUPER_STAGES} стадий, найдено {len(stages)}", "PixelShaderID")

    for s in stages:
        base = f"Stage{s.index}"
        tex = s.texture
        if tex is None:
            add(Severity.ERROR, "E210", "У стадии нет texture", f"{base}.texture")
        elif tex.strip() == "":
            add(Severity.WARNING, "W212", "Текстура не назначена (пустой путь)", f"{base}.texture")
        elif is_procedural(tex):
            err = check_procedural(tex)
            if err:
                add(Severity.ERROR, "E211", err, f"{base}.texture")
        else:
            _check_path(tex, base, project_root, add)
        if s.uv_source is None:
            add(Severity.WARNING, "W210", "Нет uvSource", f"{base}.uvSource")
        if s.uv_node is None:
            add(Severity.ERROR, "E220", "Нет класса uvTransform", f"{base}.uvTransform")
        else:
            for k in UV_KEYS:
                v = s.uv(k)
                if v is None:
                    add(Severity.ERROR, "E221", f"uvTransform: нет {k}[]", f"{base}.uvTransform.{k}")
                elif len(v) != 3:
                    add(Severity.ERROR, "E222", f"uvTransform.{k} должен содержать 3 числа", f"{base}.uvTransform.{k}")

    if expected_state is not None:
        s3 = m.stage(CONDITION_STAGE)
        want = default_stage3(expected_state)
        if s3 and s3.texture != want:
            add(Severity.WARNING, "W300",
                f"Для состояния {expected_state.value} ожидается Stage3 = {want}", "Stage3.texture")
        if not expected_state.confirmed:
            add(Severity.INFO, "I300", "Правило Stage3 для base не подтверждено образцами", "Stage3.texture")
    return sorted(out, key=lambda i: -i.severity)


def _check_path(tex: str, base: str, root: Optional[Path], add):
    loc = f"{base}.texture"
    m = re.match(r"^([A-Za-z]):[\\/](.*)$", tex)
    if m and m.group(1).upper() == "P":                      # полный путь с диском P: — допустим (Workbench), но не стандартен
        add(Severity.INFO, "I230", "Путь с диском P:\\ (полный путь). Стандартный формат RVMAT — без диска; если игра/Binarize не найдёт файл, отключите «Путь с диском» в экспорте", loc)
        tex = m.group(2)
    elif m or tex.startswith("\\\\"):
        add(Severity.ERROR, "E230", "Абсолютный путь Windows; нужен игровой путь (dz\\...)", loc)
        return
    if "/" in tex:
        add(Severity.WARNING, "W230", "Прямые слэши: в RVMAT принято использовать '\\'", loc)
    if tex.startswith(("\\", "/")):
        add(Severity.WARNING, "W231", "Путь начинается с разделителя", loc)
    ext = Path(tex.replace("\\", "/")).suffix.lower()
    if ext not in (".paa", ".pac"):
        add(Severity.WARNING, "W232", f"Игра ожидает .paa (найдено {ext or 'без расширения'})", loc)
    if root is not None:
        if resolve_physical(tex, root) is None:
            src = resolve_physical(tex, root, allow_source=True)
            if src is not None:
                add(Severity.INFO, "I233", f"Найден исходник {src.name}: при экспорте будет сконвертирован в PAA", loc)
            else:
                add(Severity.WARNING, "W233", f"Файл не найден в {root}", loc)


def validate_kit(kit: dict[str, Material], name: str,
                 states=ALL_STATES, project_root: Optional[Path] = None) -> list[Issue]:
    from .kit import kit_filename
    out: list[Issue] = []
    for st in states:
        fn = kit_filename(name, st)
        m = kit.get(fn)
        if m is None:
            out.append(Issue(Severity.ERROR, "E400", f"В комплекте нет {fn}", fn))
            continue
        for i in validate(m, project_root, st):
            i.location = f"{fn}:{i.location}"
            out.append(i)
    return sorted(out, key=lambda i: -i.severity)
