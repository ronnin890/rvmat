"""«Смысловые» параметры материала поверх реальных полей RVMAT.

Ползунки художника (шероховатость, блеск, металличность, свечение) — это не новые параметры формата:
каждый однозначно записывается в настоящие поля RVMAT (specularPower, specular, diffuse, ambient, emmisive)
и считывается из них обратно. Параметра «металличность» или «сила нормали» в самом RVMAT нет
(у DayZ это делает текстура SMDI / nohq) — подробности в описании каждого ползунка.
"""
from __future__ import annotations

import math
from typing import Sequence

from ..backend import Material

MAX_POWER = 200.0          # specularPower при шероховатости 0
DIFFUSE_BASE = 0.9         # типичный diffuse/ambient в образцах eyemask_*
METAL_SPEC_MIN, METAL_SPEC_SPAN = 0.1, 0.9
METAL_DIFFUSE_DROP = 0.8   # на сколько металличность гасит diffuse (M=1 → diffuse = 0.18)


def _clamp(x, lo, hi):
    return max(lo, min(hi, x))


def mean_rgb(v: Sequence[float]) -> float:
    return sum(v[:3]) / 3.0


def scale_rgb(v: Sequence[float], target_mean: float) -> list:
    """RGBA с новой средней яркостью RGB; оттенок и альфа сохраняются."""
    v = [float(x) for x in v] if v else [0, 0, 0, 1]
    m = mean_rgb(v)
    t = max(0.0, target_mean)
    rgb = [t, t, t] if m < 1e-6 else [x * t / m for x in v[:3]]
    return [round(_clamp(x, 0, 4), 6) for x in rgb] + [v[3] if len(v) > 3 else 1.0]


# ---- шероховатость <-> specularPower (логарифмическая шкала: на глаз равномерная)
def power_to_roughness(power: float) -> float:
    p = _clamp(float(power or 1.0), 1.0, MAX_POWER)
    return 100.0 * (1.0 - math.log(p) / math.log(MAX_POWER))


def roughness_to_power(r: float) -> float:
    return round(MAX_POWER ** (1.0 - _clamp(r, 0, 100) / 100.0), 2)


# ---- чтение (0..100)
def roughness(m: Material) -> float:
    return power_to_roughness(m.get("specularPower") or 1.0)


def shine(m: Material) -> float:
    return _clamp(mean_rgb(m.get("specular") or [0, 0, 0]) * 100.0, 0, 100)


def metallic(m: Material) -> float:
    d = mean_rgb(m.get("diffuse") or [DIFFUSE_BASE] * 3)
    return _clamp((1.0 - d / DIFFUSE_BASE) / METAL_DIFFUSE_DROP, 0, 1) * 100.0


def glow(m: Material) -> float:
    return _clamp(mean_rgb(m.get("emmisive") or [0, 0, 0]) * 100.0, 0, 100)


# ---- запись: возвращают {путь RVMAT: значение}
def set_roughness(m: Material, r: float) -> dict:
    return {"specularPower": roughness_to_power(r)}


def set_shine(m: Material, s: float) -> dict:
    return {"specular": scale_rgb(m.get("specular"), _clamp(s, 0, 100) / 100.0)}


def set_metallic(m: Material, mv: float) -> dict:
    """Металл в RVMAT: сильный блик + приглушённый рассеянный цвет."""
    k = _clamp(mv, 0, 100) / 100.0
    d_new = DIFFUSE_BASE * (1.0 - METAL_DIFFUSE_DROP * k)
    d_old = mean_rgb(m.get("diffuse") or [DIFFUSE_BASE] * 3)
    a_old = mean_rgb(m.get("ambient") or [DIFFUSE_BASE] * 3)
    a_new = a_old * d_new / d_old if d_old > 1e-6 else d_new
    return {"diffuse": scale_rgb(m.get("diffuse") or [DIFFUSE_BASE] * 3 + [1], d_new),
            "ambient": scale_rgb(m.get("ambient") or [DIFFUSE_BASE] * 3 + [1], a_new),
            "specular": scale_rgb(m.get("specular"), METAL_SPEC_MIN + METAL_SPEC_SPAN * k)}


def set_glow(m: Material, g: float) -> dict:
    return {"emmisive": scale_rgb(m.get("emmisive"), _clamp(g, 0, 100) / 100.0)}


# описание для интерфейса: ключ -> (название, подсказка слева, справа, затрагиваемые поля RVMAT, подробное пояснение)
CONTROLS = {
    "rough": ("Шероховатость", "гладкий, резкий блик", "матовый, широкий блик", ["specularPower"],
              "Пишет specularPower (чем шероховатее, тем он меньше)."),
    "shine": ("Сила блика", "нет блика", "яркий блик", ["specular"],
              "Пишет specular (яркость блика; оттенок сохраняется)."),
    "metal": ("Металличность", "пластик, дерево…", "металл", ["diffuse", "ambient", "specular"],
              "Макрос: поднимает specular и приглушает diffuse/ambient — так металл выглядит в RVMAT. "
              "Точная металличность по пикселям задаётся текстурой SMDI, а не RVMAT."),
    "glow": ("Свечение", "нет", "сильное", ["emmisive"], "Пишет emmisive (собственное свечение)."),
}
