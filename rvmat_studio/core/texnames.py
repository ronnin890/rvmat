"""Имена и пути текстур (только стандартная библиотека)."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Optional

SOURCE_EXT = {".png", ".tga", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff"}
GAME_EXT = {".paa", ".pac"}
ALL_EXT = SOURCE_EXT | GAME_EXT
SUFFIX_STAGE = {"nohq": 1, "no": 1, "nopx": 1, "as": 4, "smdi": 5, "dt": 2, "mc": 3}
BASE_SUFFIXES = {"co", "ca"}
BASE_STAGE = 0
STAGE_SUFFIX = {1: "nohq", 2: "dt", 3: "mc", 4: "as", 5: "smdi"}      # суффикс экспортируемого файла стадии
STAGE_ROLE = {1: "Normal (nohq)", 2: "Detail (dt)", 3: "Material Condition (mc)",
              4: "AS — Ambient Shadow (фоновое затенение)", 5: "SMDI", 6: "Fresnel", 7: "Environment"}
_SUFFIX_RE = re.compile(r"^(?P<base>.+?)_(?P<suf>nohq|nopx|no|as|smdi|co|ca|dt|mc|sm|mca)$", re.I)

_TRANSLIT = dict(zip("абвгдежзийклмнопрстуфхцчшщъыьэюя", ["a", "b", "v", "g", "d", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p", "r", "s", "t", "u", "f", "h", "c", "ch", "sh", "sch", "", "y", "", "e", "yu", "ya"]))


def safe_name(s: str, keep_case: bool = True) -> str:
    """Имя файла для игры: латиница/цифры/_/-, кириллица транслитерируется, остальное → '_'."""
    out = []
    for ch in s.strip():
        lo = ch.lower()
        if lo in _TRANSLIT:
            t = _TRANSLIT[lo]
            out.append(t.upper() if ch != lo and t else t)
        elif re.fullmatch(r"[A-Za-z0-9_\-]", ch):
            out.append(ch)
        elif ch == ".":
            out.append("_")
        else:
            out.append("_")
    r = re.sub(r"_+", "_", "".join(out)).strip("_")
    return (r if keep_case else r.lower()) or "material"


def split_suffix(stem: str) -> tuple[str, Optional[str]]:
    m = _SUFFIX_RE.match(stem)
    return (m["base"], m["suf"].lower()) if m else (stem, None)


def guess_stage(path) -> Optional[int]:
    suf = split_suffix(Path(path).stem)[1]
    if suf in BASE_SUFFIXES:
        return BASE_STAGE
    return SUFFIX_STAGE.get(suf) if suf else None


def scan_folder(folder, recursive=True) -> list[Path]:
    f = Path(folder)
    it = f.rglob("*") if recursive else f.glob("*")
    return sorted(p for p in it if p.is_file() and p.suffix.lower() in ALL_EXT)


def auto_match(files: list[Path]) -> dict[str, dict[int, Path]]:
    """{общее имя: {стадия: файл}}; стадия 0 — Base Color (_co/_ca), в RVMAT не пишется."""
    groups: dict[str, dict[int, Path]] = {}
    for f in files:
        base, suf = split_suffix(f.stem)
        st = guess_stage(f)
        if suf is None or st is None:
            continue
        g = groups.setdefault(base.lower(), {})
        if st == BASE_STAGE and suf == "ca" and st in g:
            continue
        g[st] = f
    return groups


def to_game_path(physical, root, ext: str = ".paa") -> str:
    rel = Path(physical).resolve().relative_to(Path(root).resolve())
    return str(rel.with_suffix(ext)).replace("/", "\\")


def unique_game_path(phys, tex_map: dict) -> str:
    """Игровой путь для файла вне корня проекта: короткое имя, при совпадении — с именами родительских папок."""
    phys = Path(phys)
    name = phys.with_suffix(".paa").name
    parents = [p for p in reversed(phys.parent.parts) if p not in ("/", "\\") and not p.endswith(":\\") and not p.endswith(":")]
    cand, depth = name, 0
    while cand.lower() in tex_map and Path(tex_map[cand.lower()]) != phys:
        depth += 1
        cand = "\\".join(reversed(parents[:depth])) + "\\" + name if depth <= len(parents) else f"{depth - len(parents)}_{name}"
    return cand


def _walk_ci(base: Path, parts: list[str]) -> Optional[Path]:
    cur = base
    for i, part in enumerate(parts):
        if not cur.is_dir():
            return None
        hit = next((c for c in cur.iterdir() if c.name.lower() == part.lower()), None)
        if hit is None and i == len(parts) - 1:                      # допускаем исходник вместо .paa
            stem = Path(part).stem.lower()
            hit = next((c for c in cur.iterdir() if c.stem.lower() == stem and c.suffix.lower() in SOURCE_EXT), None)
        if hit is None:
            return None
        cur = hit
    return cur


def find_texture(tex: str, project_root: str = "", mapping: Optional[dict] = None, extra_dirs=(),
                 allowed=None, warn=None) -> Optional[Path]:
    """Игровой путь из RVMAT -> физический файл (регистр не важен; .png вместо .paa допускается).

    Безопасность: пути с «..», UNC/устройства отклоняются; найденный файл обязан лежать внутри корня, из которого
    он найден (ссылки наружу не принимаются); абсолютные пути принимаются только внутри `allowed` (список корней;
    None — без ограничения, для обратной совместимости). Записи `mapping` — явный выбор пользователя, их не проверяем.
    Причина отклонения передаётся в warn(текст)."""
    from .safeio import has_parent_ref, is_unc_or_device, is_within
    if not tex or tex.startswith("#("):
        return None
    norm = tex.replace("/", "\\")
    key = norm.strip("\\").lower()
    mp = {k.lower(): v for k, v in (mapping or {}).items()}.get(key)
    if mp and Path(mp).is_file():
        return Path(mp)
    if is_unc_or_device(norm) or has_parent_ref(norm):
        if warn:
            warn(f"Путь текстуры отклонён (UNC или «..»): {tex}")
        return None
    parts = [x for x in norm.strip("\\").split("\\") if x]
    for root in ([project_root] if project_root else []) + list(extra_dirs):
        if root and Path(root).is_dir():
            hit = _walk_ci(Path(root), parts)
            if hit is not None and hit.is_file():
                if not is_within(hit, root):
                    if warn:
                        warn(f"Текстура {tex} указывает за пределы корня проекта (ссылка) — отклонена")
                    continue
                return hit
    p = Path(tex)
    if p.is_absolute() and p.is_file():
        if allowed is not None and not any(is_within(p, a) for a in allowed if a):
            if warn:
                warn(f"Абсолютный путь вне корней проекта отклонён: {tex}")
            return None
        return p
    return None


def convert_with_tool(src: Path, image_to_paa: str, out: Optional[Path] = None) -> Path:
    """ImageToPAA.exe из DayZ Tools (Windows)."""
    from . import dayztools
    out = out or src.with_suffix(".paa")
    exe = dayztools.validate_exe(image_to_paa, "image_to_paa")          # существует, имя ImageToPAA.exe, подтверждён
    try:
        r = subprocess.run([str(exe), str(src), str(out)], capture_output=True, text=True, timeout=300, shell=False)
    except subprocess.TimeoutExpired as e:
        raise RuntimeError("ImageToPAA не ответил за 300 с") from e
    if r.returncode != 0 or not out.exists():
        raise RuntimeError(f"ImageToPAA завершился с кодом {r.returncode}: {r.stderr or r.stdout}")
    return out
