"""Менеджер текстур: загрузка, каналы, автосопоставление, кэш миниатюр, конвертация PAA."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .logs import log_exc
from .texnames import convert_with_tool as convert_to_paa  # noqa: F401

import struct

import numpy as np
from PIL import Image

from . import limits

limits.apply_pillow_limits()

from .paa import PaaError, read_paa

from .texnames import (ALL_EXT, BASE_STAGE, BASE_SUFFIXES, GAME_EXT, SOURCE_EXT, STAGE_ROLE, SUFFIX_STAGE, auto_match, guess_stage,  # noqa: F401
                       scan_folder, split_suffix, to_drive_path, to_game_path, unique_game_path)


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


class TextureError(ValueError):
    """Текстуру нельзя прочитать (повреждена, слишком велика, неизвестный формат)."""


def load_rgba(path) -> tuple[np.ndarray, str]:
    """Прочитать текстуру в RGBA uint8. Лимиты размера файла и числа пикселей проверяются до декодирования;
    любые ошибки данных → PaaError / TextureError (оба — ValueError)."""
    p = Path(path)
    try:
        if p.suffix.lower() in GAME_EXT:
            try:
                raw = limits.read_limited(p, limits.MAX_PAA_FILE, "PAA")
            except limits.LimitError as e:
                raise PaaError(str(e)) from e
            return read_paa(raw, p.name)
        limits.check_file_size(p, limits.MAX_IMAGE_FILE, "Изображение")
        with Image.open(p) as im:
            limits.check_image_size(*im.size, what="Изображение")
            fmt = im.format or p.suffix[1:].upper()
            return np.asarray(im.convert("RGBA")).copy(), fmt
    except (PaaError, TextureError):
        raise
    except (limits.LimitError, Image.DecompressionBombError, Image.DecompressionBombWarning, SyntaxError, EOFError,
            struct.error, IndexError, KeyError, ValueError, TypeError, OverflowError, MemoryError, ZeroDivisionError) as e:
        raise TextureError(f"Не удалось прочитать изображение: {e or type(e).__name__}") from e
    except OSError as e:                      # includes UnidentifiedImageError, truncated files
        if isinstance(e, FileNotFoundError):
            raise
        raise TextureError(f"Не удалось прочитать изображение: {e}") from e


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
    except (PaaError, OSError, ValueError, Image.DecompressionBombError) as e:   # TextureError — ValueError
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
    """Кэш миниатюр. get() — синхронно; peek() — без блокировки: если миниатюры нет, её считает фоновый поток
    (текстуры 4K/8K не подвешивают окно), а `version` растёт, когда появились новые."""

    def __init__(self, size=96):
        import threading
        self.size, self._c = size, {}
        self.version = 0
        self._todo: list = []
        self._lock = threading.Lock()
        self._worker = None

    @staticmethod
    def _key(path):
        try:
            return (str(path), Path(path).stat().st_mtime_ns)
        except OSError:
            return None

    def get(self, path: Path) -> Optional[np.ndarray]:
        key = self._key(path)
        if key is None:
            return None
        if key not in self._c:
            try:
                self._c[key] = thumbnail(path, self.size)
            except Exception:
                log_exc("textures.get")
                self._c[key] = None
        return self._c[key]

    def peek(self, path: Path) -> Optional[np.ndarray]:
        key = self._key(path)
        if key is None:
            return None
        if key in self._c:
            return self._c[key]
        with self._lock:
            if all(k != key for _, k in self._todo):
                self._todo.append((Path(path), key))
            if self._worker is None or not self._worker.is_alive():
                import threading
                self._worker = threading.Thread(target=self._run, daemon=True)
                self._worker.start()
        return None

    def _run(self):
        while True:
            with self._lock:
                if not self._todo:
                    return
                path, key = self._todo.pop(0)
            try:
                arr = thumbnail(path, self.size)
            except Exception:
                log_exc("textures._run")
                arr = None
            self._c[key] = arr
            self.version += 1
