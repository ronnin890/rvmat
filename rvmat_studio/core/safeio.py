"""Безопасный ввод-вывод: проверка путей, атомарная запись, чтение JSON с лимитами, проверка схем."""
from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Optional

from . import limits


# ------------------------------------------------------------------ пути
class PathError(ValueError):
    """Путь выходит за пределы разрешённой папки или недопустим."""


def _resolve(p) -> Path:
    """resolve() без требования существования: символические ссылки в существующей части пути раскрываются."""
    return Path(os.path.expanduser(str(p))).resolve()


def is_within(path, root) -> bool:
    try:
        Path(os.path.normcase(str(_resolve(path)))).relative_to(Path(os.path.normcase(str(_resolve(root)))))
        return True
    except (ValueError, OSError, RuntimeError):
        return False


def ensure_within(path, root) -> Path:
    """Вернуть разрешённый (resolve) путь, если он лежит внутри root; иначе PathError.
    Символические ссылки раскрываются, поэтому ссылка наружу не обходит проверку."""
    if not str(path) or "\x00" in str(path):
        raise PathError("Недопустимый путь")
    if not is_within(path, root):
        raise PathError(f"Путь вне разрешённой папки: {path}")
    return _resolve(path)


def is_unc_or_device(p: str) -> bool:
    s = str(p).replace("/", "\\")
    return s.startswith("\\\\") or s.startswith("\\\\?\\") or s.startswith("\\\\.\\")


def has_parent_ref(p: str) -> bool:
    return ".." in re.split(r"[\\/]+", str(p))


# ------------------------------------------------------------------ запись
def atomic_write(path, data, backup: bool = False) -> Path:
    """Записать во временный файл рядом и атомарно заменить целевой (os.replace).
    backup=True — предварительно копирует существующий файл в <имя>.bak. При ошибке исходный файл не меняется."""
    path = Path(path)
    if isinstance(data, str):
        data = data.encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    if backup and path.exists() and path.is_file():
        bak = path.with_name(path.name + ".bak")
        _atomic_replace(bak, path.read_bytes())
    _atomic_replace(path, data)
    return path


def _atomic_replace(path: Path, data: bytes) -> None:
    fd, tmp = tempfile.mkstemp(prefix="." + path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ------------------------------------------------------------------ чтение
class DataError(ValueError):
    """Файл данных (JSON) повреждён или не соответствует схеме."""


def read_json(path, what: str = "Файл", limit: Optional[int] = None) -> Any:
    try:
        raw = limits.read_limited(path, limit or limits.MAX_JSON_FILE, what)
    except limits.LimitError as e:
        raise DataError(str(e)) from e
    try:
        return json.loads(raw.decode("utf-8-sig"))
    except (ValueError, RecursionError, UnicodeError) as e:
        raise DataError(f"{what}: повреждённый JSON ({e})") from e


# ------------------------------------------------------------------ схема
class SchemaError(DataError):
    pass


def as_dict(v, name: str, default=None) -> dict:
    if v is None:
        return {} if default is None else default
    if not isinstance(v, dict):
        raise SchemaError(f"Поле «{name}»: ожидался объект, получено {type(v).__name__}")
    return v


def as_list(v, name: str, maxn: int, default=None) -> list:
    if v is None:
        return [] if default is None else default
    if not isinstance(v, list):
        raise SchemaError(f"Поле «{name}»: ожидался список, получено {type(v).__name__}")
    if len(v) > maxn:
        raise SchemaError(f"Поле «{name}»: слишком много элементов ({len(v)} > {maxn})")
    return v


def as_str(v, name: str, default: str = "", maxlen: int = limits.MAX_STRING) -> str:
    if v is None:
        return default
    if not isinstance(v, str):
        raise SchemaError(f"Поле «{name}»: ожидалась строка, получено {type(v).__name__}")
    if len(v) > maxlen:
        raise SchemaError(f"Поле «{name}»: строка слишком длинная ({len(v)} > {maxlen})")
    if "\x00" in v:
        raise SchemaError(f"Поле «{name}»: недопустимый символ")
    return v


def as_int(v, name: str, default: int = 0, lo: Optional[int] = None, hi: Optional[int] = None) -> int:
    if v is None:
        return default
    if isinstance(v, bool) or not isinstance(v, (int, float)) or (isinstance(v, float) and v != v):
        raise SchemaError(f"Поле «{name}»: ожидалось число")
    v = int(v)
    if (lo is not None and v < lo) or (hi is not None and v > hi):
        raise SchemaError(f"Поле «{name}»: значение вне диапазона [{lo}; {hi}]")
    return v


def as_float(v, name: str, default: float = 0.0, lo: Optional[float] = None, hi: Optional[float] = None) -> float:
    if v is None:
        return default
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v or v in (float("inf"), float("-inf")):
        raise SchemaError(f"Поле «{name}»: ожидалось конечное число")
    v = float(v)
    if (lo is not None and v < lo) or (hi is not None and v > hi):
        raise SchemaError(f"Поле «{name}»: значение вне диапазона [{lo}; {hi}]")
    return v


def as_bool(v, name: str, default: bool = False) -> bool:
    if v is None:
        return default
    if not isinstance(v, bool):
        raise SchemaError(f"Поле «{name}»: ожидалось логическое значение")
    return v


def str_dict(v, name: str, maxn: int = limits.MAX_TEX_MAP) -> dict:
    """{str: str} с лимитами."""
    d = as_dict(v, name)
    if len(d) > maxn:
        raise SchemaError(f"Поле «{name}»: слишком много записей ({len(d)} > {maxn})")
    return {as_str(k, name + " (ключ)"): as_str(x, name) for k, x in d.items()}


def str_list(v, name: str, maxn: int = limits.MAX_TEX_MAP) -> list:
    return [as_str(x, name) for x in as_list(v, name, maxn)]
