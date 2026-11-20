"""Генерация комплекта связанных материалов (base/damage/destruct/decorative/...)

Правила Stage3 подтверждены образцами eyemask_*:
  decorative            -> #(argb,8,8,3)color(0,0,0,0,MC)
  *_damage              -> dz\\characters\\data\\generic_damage_mc.paa
  *_destruct            -> dz\\characters\\data\\generic_destruct_mc.paa

ВНИМАНИЕ: обычный (base) материал в образцах отсутствует. Его Stage3 здесь
взят по аналогии с decorative и помечен ``confirmed=False`` — подтвердите
по реальному base-файлу (см. KitSpec.stage3_overrides / base_template).
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any, Optional

from .model import Material
from .procedural import make_color

DAMAGE_MC = r"dz\characters\data\generic_damage_mc.paa"
DESTRUCT_MC = r"dz\characters\data\generic_destruct_mc.paa"
PROCEDURAL_MC = make_color(0, 0, 0, 0, "MC")
CONDITION_STAGE = 3


class State(str, enum.Enum):
    BASE = "base"
    DAMAGE = "damage"
    DESTRUCT = "destruct"
    DECORATIVE = "decorative"
    DECORATIVE_DAMAGE = "decorative_damage"
    DECORATIVE_DESTRUCT = "decorative_destruct"

    @property
    def suffix(self) -> str:
        return "" if self is State.BASE else "_" + self.value

    @property
    def is_decorative(self) -> bool:
        return self.value.startswith("decorative")

    @property
    def confirmed(self) -> bool:
        """Подтверждено ли правило Stage3 образцами."""
        return self is not State.BASE


ALL_STATES = tuple(State)
_STAGE3 = {
    State.BASE: PROCEDURAL_MC,
    State.DECORATIVE: PROCEDURAL_MC,
    State.DAMAGE: DAMAGE_MC,
    State.DECORATIVE_DAMAGE: DAMAGE_MC,
    State.DESTRUCT: DESTRUCT_MC,
    State.DECORATIVE_DESTRUCT: DESTRUCT_MC,
}


def kit_filename(name: str, state: State) -> str:
    return f"{name}{state.suffix}.rvmat"


def default_stage3(state: State) -> str:
    return _STAGE3[state]


@dataclass
class KitSpec:
    """Общая основа + индивидуальные переопределения по состояниям.

    ``shared`` меняется один раз и распространяется на все варианты, кроме тех,
    где путь переопределён в ``overrides[state]``.

    Ключи переопределений: "ambient", "specularPower", "Stage1.texture",
    "Stage2.Filter", "Stage4.uvTransform.pos" ...
    """
    name: str
    shared: Material
    overrides: dict[State, dict[str, Any]] = field(default_factory=dict)
    # переопределения, общие для всех decorative_* состояний (напр. другие текстуры);
    # применяются поверх shared, но ниже индивидуальных overrides состояния
    decorative: dict[str, Any] = field(default_factory=dict)

    def render(self, state: State) -> Material:
        m = self.shared.clone()
        st3 = m.stage(CONDITION_STAGE)
        if st3 is None:
            st3 = m.add_stage(CONDITION_STAGE, default_stage3(state))
        st3.texture = default_stage3(state)
        if state.is_decorative:
            for path, val in self.decorative.items():
                apply_override(m, path, val)
        for path, val in self.overrides.get(state, {}).items():
            apply_override(m, path, val)
        return m

    def render_all(self, states=ALL_STATES) -> dict[str, Material]:
        return {kit_filename(self.name, s): self.render(s) for s in states}


def apply_override(m: Material, path: str, val: Any):
    parts = path.split(".")
    if len(parts) == 1:
        cur = m.root.get_prop(path)
        if isinstance(val, str):
            m.set_string(path, val)
        elif isinstance(val, (list, tuple)):
            m.set_color(path, val)
        else:
            m.set_scalar(path, val)
        return
    if not parts[0].lower().startswith("stage"):
        raise KeyError(path)
    idx = int(parts[0][5:])
    st = m.stage(idx) or m.add_stage(idx, "")
    if parts[1].lower() == "texture":
        st.texture = val
    elif parts[1].lower() == "uvsource":
        st.uv_source = val
    elif parts[1].lower() == "filter":
        st.filter = val
    elif parts[1] == "uvTransform" and len(parts) == 3:
        st.set_uv(parts[2], val)
    else:
        raise KeyError(path)


def diff_materials(a: Material, b: Material) -> list[tuple[str, Any, Any]]:
    """Список различий (путь, значение_a, значение_b) для показа в UI."""
    def flat(m: Material) -> dict[str, str]:
        out: dict[str, str] = {}

        def walk(node, prefix):
            for n in node.body:
                if hasattr(n, "value"):
                    out[prefix + n.name] = str(n.value)
                elif hasattr(n, "body") and not hasattr(n, "text"):
                    walk(n, prefix + n.name + ".")
        walk(m.root, "")
        return out
    fa, fb = flat(a), flat(b)
    return [(k, fa.get(k), fb.get(k)) for k in sorted(set(fa) | set(fb)) if fa.get(k) != fb.get(k)]


def flatten_values(m: Material) -> dict[str, Any]:
    """Плоский словарь путь -> значение (числа/списки/строки) для сравнения и импорта."""
    out: dict[str, Any] = {}

    def walk(node, prefix):
        for n in node.body:
            if hasattr(n, "value"):
                v = n.value
                out[prefix + n.name] = (v.floats() if v.kind == "array" and all(i.kind == "num" for i in v.items)
                                        else (float(v.raw) if v.kind == "num" else v.text))
            elif hasattr(n, "body"):
                walk(n, prefix + n.name + ".")
    walk(m.root, "")
    return out


def get_value(m: Material, path: str):
    return flatten_values(m).get(path)
