"""Менеджер текстур: загрузка, каналы, автосопоставление, кэш миниатюр, конвертация PAA."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .texnames import convert_with_tool as convert_to_paa  # noqa: F401

import numpy as np
from PIL import Image

from .paa import PaaError, read_paa

from .texnames import (ALL_EXT, BASE_STAGE, BASE_SUFFIXES, GAME_EXT, SOURCE_EXT, STAGE_ROLE, SUFFIX_STAGE, auto_match, guess_stage,  # noqa: F401
                       scan_folder, split_suffix, to_game_path, unique_game_path)


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
        return read_paa(p.read_bytes(), p.name)
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


