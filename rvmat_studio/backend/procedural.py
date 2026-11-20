"""Процедурные текстуры RVMAT: ``#(argb,8,8,3)color(r,g,b,a,TAG)``, ``#(ai,32,128,1)fresnel(n,k)``.

Подтверждены образцами: color(...,DT|MC) и fresnel(n,k). Прочие функции
(например ``perlin``, ``irradiance``) разбираются как ``Generic`` и
сохраняются без изменений.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional, Union

from .parser import format_number

_RE = re.compile(r"^#\((?P<fmt>[^)]*)\)(?P<fn>[A-Za-z_]\w*)\((?P<args>.*)\)$", re.S)


def is_procedural(texture: str) -> bool:
    return texture.lstrip().startswith("#(")


@dataclass
class Procedural:
    fmt: str                       # "argb,8,8,3"
    func: str                      # "color" | "fresnel" | ...
    args: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        return f"#({self.fmt}){self.func}({','.join(self.args)})"

    @property
    def tag(self) -> Optional[str]:
        """DT / MC / ... для color()."""
        return self.args[-1] if self.func == "color" and len(self.args) == 5 else None

    @property
    def rgba(self) -> Optional[tuple[float, float, float, float]]:
        if self.func == "color" and len(self.args) == 5:
            return tuple(float(a) for a in self.args[:4])  # type: ignore
        return None

    @property
    def fresnel(self) -> Optional[tuple[float, float]]:
        if self.func == "fresnel" and len(self.args) == 2:
            return float(self.args[0]), float(self.args[1])
        return None


def parse_procedural(texture: str) -> Procedural:
    m = _RE.match(texture.strip())
    if not m:
        raise ValueError(f"Некорректная процедурная текстура: {texture!r}")
    args = [a.strip() for a in m["args"].split(",")] if m["args"].strip() else []
    return Procedural(m["fmt"].replace(" ", ""), m["fn"], args)


def check_procedural(texture: str) -> Optional[str]:
    """None если корректна, иначе текст ошибки."""
    try:
        p = parse_procedural(texture)
    except ValueError as e:
        return str(e)
    fmt = p.fmt.split(",")
    if p.func == "color":
        if len(p.args) != 5:
            return "color() ожидает 5 аргументов: r,g,b,a,TAG"
        try:
            vals = [float(a) for a in p.args[:4]]
        except ValueError:
            return "color(): r,g,b,a должны быть числами"
        if any(not 0.0 <= v <= 1.0 for v in vals):
            return "color(): компоненты r,g,b,a должны быть в диапазоне 0..1"
        if not re.fullmatch(r"[A-Z]{1,4}", p.args[4]):
            return f"color(): неожиданный тег {p.args[4]!r} (ожидается, например, DT или MC)"
    elif p.func == "fresnel":
        if len(p.args) != 2:
            return "fresnel() ожидает 2 аргумента: n,k"
        try:
            [float(a) for a in p.args]
        except ValueError:
            return "fresnel(): аргументы должны быть числами"
    if len(fmt) != 4 or not fmt[0] or not all(x.isdigit() for x in fmt[1:]):
        return f"Некорректный формат ({p.fmt}); ожидается тип,ширина,высота,мипы"
    return None


def make_color(r: float, g: float, b: float, a: float, tag: str) -> str:
    n = [format_number(x) for x in (r, g, b, a)]
    return f"#(argb,8,8,3)color({','.join(n)},{tag})"


def make_fresnel(n: float, k: float) -> str:
    return f"#(ai,32,128,1)fresnel({format_number(n)},{format_number(k)})"
