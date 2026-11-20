"""Менеджер текстур: загрузка, каналы, автосопоставление, кэш миниатюр, конвертация PAA."""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image

from .paa import PaaError, read_paa

SOURCE_EXT = {".png", ".tga", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"}
GAME_EXT = {".paa", ".pac"}
ALL_EXT = SOURCE_EXT | GAME_EXT

# суффикс -> номер стадии RVMAT (Super)
SUFFIX_STAGE = {"nohq": 1, "no": 1, "nopx": 1, "as": 4, "smdi": 5, "co": 4, "ca": 4}
STAGE_ROLE = {1: "Normal (nohq)", 2: "Detail (dt)", 3: "Material Condition (mc)",
              4: "AS (ambient shadow / color)", 5: "SMDI", 6: "Fresnel", 7: "Environment"}
_SUFFIX_RE = re.compile(r"^(?P<base>.+?)_(?P<suf>nohq|nopx|no|as|smdi|co|ca|dt|mc|sm|mca)$", re.I)


def split_suffix(stem: str) -> tuple[str, Optional[str]]:
    m = _SUFFIX_RE.match(stem)
    return (m["base"], m["suf"].lower()) if m else (stem, None)


def guess_stage(path) -> Optional[int]:
    suf = split_suffix(Path(path).stem)[1]
    if suf == "dt":
        return 2
    if suf == "mc":
        return 3
    return SUFFIX_STAGE.get(suf) if suf else None


@dataclass
class TextureInfo:
    path: Path
    width: int = 0
    height: int = 0
    fmt: str = ""
    has_alpha: bool = False
    error: str = ""
    exists: bool = True

    @property
    def name(self):
        return self.path.name

    @property
    def pow2(self) -> bool:
        p = lambda n: n > 0 and n & (n - 1) == 0
        return p(self.width) and p(self.height)

    def problems(self) -> list[str]:
        out = []
        if not self.exists:
            return ["Файл не найден"]
        if self.error:
            return [self.error]
        if not self.pow2:
            out.append(f"Размер {self.width}x{self.height} не степень двойки")
        if self.path.suffix.lower() in SOURCE_EXT:
            out.append("Исходный формат: перед использованием в игре нужна конвертация в .paa")
        return out


def load_rgba(path) -> tuple[np.ndarray, str]:
    p = Path(path)
    if p.suffix.lower() in GAME_EXT:
        return read_paa(p.read_bytes())
    with Image.open(p) as im:
        fmt = im.format or p.suffix[1:].upper()
        return np.asarray(im.convert("RGBA")).copy(), fmt


def probe(path) -> TextureInfo:
    p = Path(path)
    ti = TextureInfo(p)
    if not p.exists():
        ti.exists = False
        return ti
    try:
        arr, fmt = load_rgba(p)
        ti.height, ti.width = arr.shape[:2]
        ti.fmt = fmt
        ti.has_alpha = bool((arr[:, :, 3] < 255).any())
    except (PaaError, OSError, ValueError, Image.DecompressionBombError) as e:
        ti.error = f"Не удалось прочитать: {e}"
    return ti


def channel_view(rgba: np.ndarray, mode: str) -> np.ndarray:
    """Вернуть RGBA для просмотра: rgb, r, g, b, a, normal (проверка Z), swapg (инверт. G)."""
    out = rgba.copy()
    if mode == "rgb":
        out[:, :, 3] = 255
    elif mode in ("r", "g", "b", "a"):
        ch = rgba[:, :, "rgba".index(mode)]
        out = np.stack([ch, ch, ch, np.full_like(ch, 255)], -1)
    elif mode == "swapg":
        out[:, :, 1] = 255 - out[:, :, 1]
        out[:, :, 3] = 255
    return out


def normal_report(rgba: np.ndarray) -> dict:
    """Проверка нормалей: длина вектора и знак Z (для DayZ nohq: R=X, G=Y, Z восстанавливается/в B)."""
    n = rgba[:, :, :3].astype(np.float32) / 127.5 - 1
    ln = np.linalg.norm(n, axis=2)
    return {"mean_len": float(ln.mean()), "z_negative_ratio": float((n[:, :, 2] < 0).mean()),
            "mean_xy": (float(n[:, :, 0].mean()), float(n[:, :, 1].mean()))}


def to_qimage_bytes(rgba: np.ndarray):
    return np.ascontiguousarray(rgba).tobytes()


def thumbnail(path, size: int = 96) -> np.ndarray:
    arr, _ = load_rgba(path)
    im = Image.fromarray(arr)
    im.thumbnail((size, size))
    return np.asarray(im)


class ThumbCache:
    def __init__(self, size=96):
        self.size, self._c = size, {}

    def get(self, path: Path) -> Optional[np.ndarray]:
        try:
            key = (str(path), path.stat().st_mtime_ns)
        except OSError:
            return None
        if key not in self._c:
            try:
                self._c[key] = thumbnail(path, self.size)
            except Exception:
                self._c[key] = None
        return self._c[key]


def scan_folder(folder, recursive=True) -> list[Path]:
    f = Path(folder)
    it = f.rglob("*") if recursive else f.glob("*")
    return sorted(p for p in it if p.is_file() and p.suffix.lower() in ALL_EXT)


def auto_match(files: list[Path]) -> dict[str, dict[int, Path]]:
    """Группировка по общему имени: {base: {stage: path}} (по _nohq/_as/_smdi/_dt/_mc/_co)."""
    groups: dict[str, dict[int, Path]] = {}
    for f in files:
        base, suf = split_suffix(f.stem)
        st = guess_stage(f)
        if suf is None or st is None:
            continue
        g = groups.setdefault(base.lower(), {})
        # _co и _as оба претендуют на Stage4: _as предпочтительнее
        if st in g and suf != "as":
            continue
        g[st] = f
    return groups


def to_game_path(physical: Path, root: Path, ext: str = ".paa") -> str:
    """Физический путь -> игровой (относительно P:/корня проекта), с .paa."""
    rel = Path(physical).resolve().relative_to(Path(root).resolve())
    return str(rel.with_suffix(ext)).replace("/", "\\")


def convert_to_paa(src: Path, image_to_paa: Path, out: Optional[Path] = None) -> Path:
    """Вызов ImageToPAA.exe из DayZ Tools. Требует Windows + установленные DayZ Tools."""
    out = out or src.with_suffix(".paa")
    if not Path(image_to_paa).exists():
        raise FileNotFoundError(f"ImageToPAA не найден: {image_to_paa}")
    r = subprocess.run([str(image_to_paa), str(src), str(out)], capture_output=True, text=True, timeout=300)
    if r.returncode != 0 or not out.exists():
        raise RuntimeError(f"ImageToPAA завершился с кодом {r.returncode}: {r.stderr or r.stdout}")
    return out
