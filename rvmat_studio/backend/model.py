"""Высокоуровневая модель материала поверх lossless-AST."""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

from . import parser as P
from .parser import ClassNode, Value

COLOR_PARAMS = ("ambient", "diffuse", "forcedDiffuse", "emmisive", "specular")  # emmisive — написание DayZ
SCALAR_PARAMS = ("specularPower",)
STRING_PARAMS = ("PixelShaderID", "VertexShaderID")
REQUIRED_PARAMS = COLOR_PARAMS + SCALAR_PARAMS + STRING_PARAMS
UV_KEYS = ("aside", "up", "dir", "pos")
DEFAULT_UV = {"aside": (1, 0, 0), "up": (0, 1, 0), "dir": (0, 0, 0), "pos": (0, 0, 0)}
_STAGE_RE = re.compile(r"^Stage(\d+)$", re.I)


class Stage:
    """Обёртка над ClassNode StageN. Всё неизвестное остаётся в узле."""

    def __init__(self, node: ClassNode):
        self.node = node

    @property
    def index(self) -> int:
        return int(_STAGE_RE.match(self.node.name)[1])

    @property
    def texture(self) -> Optional[str]:
        p = self.node.get_prop("texture")
        return p.value.text if p else None

    @texture.setter
    def texture(self, v: str):
        self.node.set_prop("texture", Value.string(v), after=None)

    @property
    def uv_source(self) -> Optional[str]:
        p = self.node.get_prop("uvSource")
        return p.value.text if p else None

    @uv_source.setter
    def uv_source(self, v: str):
        self.node.set_prop("uvSource", Value.string(v), after="texture")

    @property
    def filter(self) -> Optional[str]:
        p = self.node.get_prop("Filter")
        return p.value.text if p else None

    @filter.setter
    def filter(self, v: Optional[str]):
        if v is None:
            self.node.remove("Filter")
        else:
            self.node.set_prop("Filter", Value.string(v))

    @property
    def uv_node(self) -> Optional[ClassNode]:
        return self.node.get_class("uvTransform")

    def uv(self, key: str) -> Optional[list[float]]:
        n = self.uv_node
        p = n.get_prop(key) if n else None
        return p.value.floats() if p else None

    def set_uv(self, key: str, values: Sequence[float]):
        if key not in UV_KEYS:
            raise KeyError(key)
        n = self.uv_node
        if n is None:
            n = ClassNode("uvTransform")
            self.node.body.append(n)
        n.set_prop(key, Value.array(values), True)

    # Удобная параметризация UV (интерпретация матрицы: u' = aside.x*u + up.x*v + pos.x, ...)
    def uv_params(self) -> dict:
        import math
        a, u, p = self.uv("aside") or [1, 0, 0], self.uv("up") or [0, 1, 0], self.uv("pos") or [0, 0, 0]
        su, sv = math.hypot(a[0], a[1]), math.hypot(u[0], u[1])
        return {"tile_u": su, "tile_v": sv, "offset_u": p[0], "offset_v": p[1],
                "rotation": math.degrees(math.atan2(a[1], a[0]))}

    def set_uv_params(self, tile_u=1.0, tile_v=1.0, offset_u=0.0, offset_v=0.0, rotation=0.0):
        import math
        c, s = math.cos(math.radians(rotation)), math.sin(math.radians(rotation))
        cur = self.uv("pos") or [0, 0, 0]
        r = lambda x: round(x, 6) + 0.0
        self.set_uv("aside", [r(tile_u * c), r(tile_u * s), 0])
        self.set_uv("up", [r(-tile_v * s), r(tile_v * c), 0])
        self.set_uv("pos", [offset_u, offset_v, cur[2]])

    def ensure_uv(self):
        for k, v in DEFAULT_UV.items():
            if self.uv_node is None or self.uv_node.get_prop(k) is None:
                self.set_uv(k, v)


class Material:
    def __init__(self, root: Optional[ClassNode] = None):
        self.root = root or ClassNode("")

    # --- I/O
    @classmethod
    def from_text(cls, text: str) -> "Material":
        return cls(P.parse(text))

    @classmethod
    def load(cls, path) -> "Material":
        p = Path(path)
        if p.stat().st_size > 2 * 1024 * 1024:
            raise ValueError("RVMAT слишком велик (максимум 2 МБ)")
        return cls.from_text(p.read_bytes().decode("utf-8-sig", "replace"))

    def to_text(self) -> str:
        return P.serialize(self.root)

    def save(self, path, backup: bool = True):
        """Запись с .bak-копией существующего файла (защита от перезаписи)."""
        path = Path(path)
        if backup and path.exists():
            path.with_suffix(path.suffix + ".bak").write_bytes(path.read_bytes())
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self.to_text().encode("utf-8"))   # без BOM, CRLF — как в DayZ Tools

    def clone(self) -> "Material":
        return Material(copy.deepcopy(self.root))

    # --- общие параметры
    def get(self, name: str):
        p = self.root.get_prop(name)
        if p is None:
            return None
        v = p.value
        return v.floats() if v.kind == "array" else (float(v.raw) if v.kind == "num" else v.text)

    def set_color(self, name: str, rgba: Sequence[float]):
        if len(rgba) != 4:
            raise ValueError("Ожидается RGBA (4 значения)")
        self.root.set_prop(name, Value.array(rgba), True)

    def set_scalar(self, name: str, x: float):
        self.root.set_prop(name, Value.num(x), False)

    def set_string(self, name: str, s: str):
        self.root.set_prop(name, Value.string(s), False)

    # --- стадии
    def stages(self) -> list[Stage]:
        st = [Stage(c) for c in self.root.classes() if _STAGE_RE.match(c.name)]
        return sorted(st, key=lambda s: s.index)

    def stage(self, i: int) -> Optional[Stage]:
        for s in self.stages():
            if s.index == i:
                return s
        return None

    def add_stage(self, i: int, texture: str, uv_source: str = "tex") -> Stage:
        if self.stage(i):
            raise ValueError(f"Stage{i} уже существует")
        node = ClassNode(f"Stage{i}")
        s = Stage(node)
        s.texture = texture
        s.uv_source = uv_source
        s.ensure_uv()
        later = [n for n in self.root.body if isinstance(n, ClassNode)
                 and _STAGE_RE.match(n.name) and int(_STAGE_RE.match(n.name)[1]) > i]
        if later:
            self.root.body.insert(self.root.body.index(later[0]), node)
        else:
            last = [n for n in self.root.body if isinstance(n, ClassNode) and _STAGE_RE.match(n.name)]
            self.root.body.insert(self.root.body.index(last[-1]) + 1 if last else len(self.root.body), node)
        return s

    def remove_stage(self, i: int) -> bool:
        return self.root.remove(f"Stage{i}")
