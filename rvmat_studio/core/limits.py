"""Единое место для лимитов на недоверенные данные (файлы моделей, текстур, проектов).

Все значения — верхние границы «разумных» входных данных; превышение даёт понятную ошибку, а не зависание
или исчерпание памяти. Менять можно здесь (или из тестов через monkeypatch модуля)."""
from __future__ import annotations

from pathlib import Path

MiB = 1024 * 1024

# --- размеры входных файлов, байт
MAX_FBX_FILE = 512 * MiB
MAX_FBX_ASCII_FILE = 64 * MiB
MAX_OBJ_FILE = 512 * MiB
MAX_PAA_FILE = 256 * MiB
MAX_IMAGE_FILE = 256 * MiB
MAX_RVMAT_FILE = 2 * MiB
MAX_JSON_FILE = 32 * MiB            # .rvproj / .rvpreset / settings.json

# --- FBX
MAX_FBX_DEPTH = 64                  # вложенность узлов
MAX_FBX_NODES = 1_000_000
MAX_FBX_PROPS = 100_000             # свойств в одном узле
MAX_FBX_ARRAY_BYTES = 128 * MiB     # размер одного распакованного массива
MAX_FBX_TOTAL_ARRAY_BYTES = 320 * MiB   # суммарно по файлу
MAX_FBX_STRING = 16 * MiB
MAX_ASCII_TOKENS = 3_000_000

# --- сетки
MAX_VERTICES = 16_000_000
MAX_TRIANGLES = 16_000_000
MAX_OBJ_LINES = 40_000_000

# --- изображения / PAA
MAX_IMAGE_DIM = 16384
MAX_IMAGE_PIXELS = 8192 * 8192      # 67 млн пикселей ≈ 268 МБ в RGBA

# --- RVMAT
MAX_RVMAT_DEPTH = 64
MAX_RVMAT_TOKENS = 100_000

# --- проекты и пресеты
MAX_SLOTS = 256
MAX_PRESETS = 5000
MAX_STRING = 4096
MAX_PARTS = 5000
MAX_TEX_MAP = 5000


class LimitError(ValueError):
    """Файл превышает допустимые пределы."""


def check_file_size(path, limit: int, what: str = "Файл") -> int:
    p = Path(path)
    try:
        size = p.stat().st_size
    except OSError as e:
        raise LimitError(f"{what}: не удалось прочитать ({e.strerror or e})") from e
    if size > limit:
        raise LimitError(f"{what} слишком велик: {size / MiB:.1f} МБ (максимум {limit / MiB:.0f} МБ)")
    return size


def read_limited(path, limit: int, what: str = "Файл") -> bytes:
    """Прочитать файл целиком, но не больше limit байт (проверка до чтения и защита от роста файла)."""
    size = check_file_size(path, limit, what)
    with open(path, "rb") as f:
        data = f.read(min(limit, size) + 1)          # не больше реального размера: f.read(N) заранее выделяет N байт
    if len(data) > limit:
        raise LimitError(f"{what} слишком велик (максимум {limit / MiB:.0f} МБ)")
    return data


def read_text_limited(path, limit: int, what: str = "Файл", encoding: str = "utf-8-sig") -> str:
    return read_limited(path, limit, what).decode(encoding, "replace")


def check_image_size(w: int, h: int, what: str = "Изображение") -> None:
    if w <= 0 or h <= 0:
        raise LimitError(f"{what}: неверный размер {w}x{h}")
    if w > MAX_IMAGE_DIM or h > MAX_IMAGE_DIM or w * h > MAX_IMAGE_PIXELS:
        raise LimitError(f"{what}: размер {w}x{h} превышает допустимый (до {MAX_IMAGE_DIM}, не более {MAX_IMAGE_PIXELS // 1_000_000} Мпикс)")


def apply_pillow_limits() -> None:
    try:
        from PIL import Image
        Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS
    except ImportError:          # pragma: no cover
        pass
