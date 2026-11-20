"""Правая панель: общие параметры, Stage1–7, UV Transform, процедурные текстуры."""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QGroupBox, QHBoxLayout,
                               QLabel, QLineEdit, QMenu, QPushButton, QScrollArea, QTabWidget, QToolButton,
                               QVBoxLayout, QWidget, QGridLayout)

from ..backend import State
from ..backend.kit import get_value
from ..backend.model import COLOR_PARAMS, UV_KEYS
from ..backend.procedural import is_procedural, make_color, make_fresnel, parse_procedural
from ..core.project import TARGET_DECOR, TARGET_SHARED, Project
from ..core.textures import STAGE_ROLE
from .widgets import ColorRow, ParamRow, pixmap_from_rgba

SHADERS = ["Super", "Normal", "NormalMap", "Multi", "Glass", "Water", "Terrain", "Detail"]
UV_SOURCES = ["tex", "tex1", "none"]
FILTERS = ["", "Trilinear", "Anisotropic", "Linear", "Point"]
COLOR_LABELS = {"ambient": "Ambient", "diffuse": "Diffuse", "forcedDiffuse": "Forced Diffuse",
                "emmisive": "Emmisive", "specular": "Specular"}


class StageEditor(QWidget):
    def __init__(self, panel: "PropertiesPanel", idx: int):
        super().__init__()
        self.panel, self.idx = panel, idx
        p = f"Stage{idx}"
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(f"<b>{STAGE_ROLE.get(idx, 'Дополнительная стадия')}</b>"))
        self.vis = QCheckBox("Показывать в предпросмотре (в RVMAT остаётся)")
        self.vis.setChecked(True)
        self.vis.toggled.connect(lambda on: panel.stage_visibility.emit(idx, on))
        lay.addWidget(self.vis)

        g = QGroupBox("Текстура")
        gl = QGridLayout(g)
        self.thumb = QLabel("—")
        self.thumb.setFixedSize(96, 96)
        self.thumb.setAlignment(Qt.AlignCenter)
        self.thumb.setStyleSheet("border:1px solid #373a45; background:#15161a;")
        self.tex = QLineEdit()
        self.tex.setPlaceholderText("игровой путь, например dz\\characters\\...\\x_as.paa")
        self.tex.editingFinished.connect(lambda: panel.set_value(f"{p}.texture", self.tex.text().strip()))
        b_file = QPushButton("Файл…")
        b_file.clicked.connect(lambda: panel.assign_requested.emit(idx))
        b_proc = QToolButton()
        b_proc.setText("Процедурная ▾")
        b_proc.setPopupMode(QToolButton.InstantPopup)
        menu = QMenu(b_proc)
        menu.addAction("color… (DT: деталь)", lambda: panel.set_value(f"{p}.texture", make_color(0.5, 0.5, 0.5, 1, "DT")))
        menu.addAction("color… (MC: условие)", lambda: panel.set_value(f"{p}.texture", make_color(0, 0, 0, 0, "MC")))
        menu.addAction("fresnel(n,k)", lambda: panel.set_value(f"{p}.texture", make_fresnel(0.88, 1.89)))
        b_proc.setMenu(menu)
        b_view = QPushButton("Просмотр…")
        b_view.clicked.connect(lambda: panel.view_requested.emit(idx))
        self.info = QLabel("")
        self.info.setObjectName("hint")
        self.info.setWordWrap(True)
        gl.addWidget(self.thumb, 0, 0, 3, 1)
        gl.addWidget(self.tex, 0, 1, 1, 3)
        b_file.setText("Файл…"); b_view.setText("Просмотр")
        brow = QHBoxLayout()
        for b_ in (b_file, b_proc, b_view):
            brow.addWidget(b_)
        gl.addLayout(brow, 1, 1, 1, 3)
        gl.addWidget(self.info, 2, 1, 1, 3)
        lay.addWidget(g)

        # процедурные параметры
        self.pg = QGroupBox("Процедурная текстура")
        pl = QHBoxLayout(self.pg)
        self.pc = [QDoubleSpinBox() for _ in range(4)]
        for s, n in zip(self.pc, "RGBA"):
            s.setRange(0, 1); s.setDecimals(4); s.setSingleStep(0.05); s.setPrefix(n + " ")
            s.valueChanged.connect(self._color_changed)
            pl.addWidget(s)
        self.ptag = QLineEdit()
        self.ptag.setMaximumWidth(60)
        self.ptag.editingFinished.connect(self._color_changed)
        pl.addWidget(QLabel("Тег"))
        pl.addWidget(self.ptag)
        self.fn = QDoubleSpinBox(); self.fk = QDoubleSpinBox()
        for s, n in ((self.fn, "n"), (self.fk, "k")):
            s.setRange(0, 20); s.setDecimals(4); s.setSingleStep(0.01); s.setPrefix(n + " = ")
            s.valueChanged.connect(self._fresnel_changed)
        pl.addWidget(self.fn); pl.addWidget(self.fk)
        lay.addWidget(self.pg)

        # UV
        ug = QGroupBox("UV Transform")
        ul = QGridLayout(ug)
        ul.addWidget(QLabel("uvSource"), 0, 0)
        self.src = QComboBox(); self.src.setEditable(True); self.src.addItems(UV_SOURCES)
        self.src.activated.connect(lambda: panel.set_value(f"{p}.uvSource", self.src.currentText()))
        self.src.lineEdit().editingFinished.connect(lambda: panel.set_value(f"{p}.uvSource", self.src.currentText()))
        ul.addWidget(self.src, 0, 1, 1, 3)
        self.uv = {}
        for r, k in enumerate(UV_KEYS, 1):
            ul.addWidget(QLabel(k), r, 0)
            row = []
            for c in range(3):
                s = QDoubleSpinBox(); s.setRange(-1000, 1000); s.setDecimals(5); s.setMinimumWidth(60); s.setSingleStep(0.1)
                s.valueChanged.connect(lambda _=0, k=k: self._uv_changed(k))
                ul.addWidget(s, r, 1 + c)
                row.append(s)
            self.uv[k] = row
        ul.addWidget(QLabel("<i>Удобно:</i>"), 5, 0)
        self.h = {}
        for c, (k, lo, hi) in enumerate((("tile_u", -64, 64), ("tile_v", -64, 64), ("offset_u", -64, 64), ("offset_v", -64, 64), ("rotation", -360, 360))):
            s = QDoubleSpinBox(); s.setRange(lo, hi); s.setDecimals(4); s.setSingleStep(0.1); s.setPrefix({"tile_u": "Tile U ", "tile_v": "Tile V ", "offset_u": "Off U ", "offset_v": "Off V ", "rotation": "Rot° "}[k])
            s.valueChanged.connect(self._helper_changed)
            self.h[k] = s
            ul.addWidget(s, 6 + c // 3, c % 3)
        lay.addWidget(ug)

        fg = QHBoxLayout()
        fg.addWidget(QLabel("Filter"))
        self.filt = QComboBox(); self.filt.setEditable(True); self.filt.addItems(FILTERS)
        self.filt.activated.connect(lambda: panel.set_value(f"{p}.Filter", self.filt.currentText() or None))
        fg.addWidget(self.filt)
        lay.addLayout(fg)
        self.extra = QLabel("")
        self.extra.setObjectName("hint")
        self.extra.setWordWrap(True)
        lay.addWidget(self.extra)
        lay.addStretch()
        self._lock = False

    # --- handlers
    def _color_changed(self, *_):
        if self._lock:
            return
        v = [s.value() for s in self.pc]
        tag = self.ptag.text().strip() or "DT"
        self.panel.set_value(f"Stage{self.idx}.texture", make_color(*v, tag))

    def _fresnel_changed(self, *_):
        if not self._lock:
            self.panel.set_value(f"Stage{self.idx}.texture", make_fresnel(self.fn.value(), self.fk.value()))

    def _uv_changed(self, k):
        if not self._lock:
            self.panel.set_value(f"Stage{self.idx}.uvTransform.{k}", [s.value() for s in self.uv[k]])

    def _helper_changed(self, *_):
        if self._lock:
            return
        import math
        h = {k: s.value() for k, s in self.h.items()}
        c, s_ = math.cos(math.radians(h["rotation"])), math.sin(math.radians(h["rotation"]))
        r = lambda x: round(x, 6) + 0.0
        p = f"Stage{self.idx}.uvTransform."
        pos = self.panel.current_value(p + "pos") or [0, 0, 0]
        self.panel.set_value(p + "aside", [r(h["tile_u"] * c), r(h["tile_u"] * s_), 0], "UV tiling")
        self.panel.set_value(p + "up", [r(-h["tile_v"] * s_), r(h["tile_v"] * c), 0], "UV tiling")
        self.panel.set_value(p + "pos", [h["offset_u"], h["offset_v"], pos[2]], "UV offset")

    # --- refresh
    def refresh(self, mat, find: Callable, thumb_cache, ov: Callable[[str], bool]):
        self._lock = True
        try:
            st = mat.stage(self.idx)
            if st is None:
                self.tex.setText("")
                self.info.setText("Стадия отсутствует в материале. Задайте текстуру, чтобы создать её.")
                self.thumb.clear(); self.thumb.setText("—")
                self.pg.setVisible(False)
                return
            tex = st.texture or ""
            self.tex.setText(tex)
            proc = is_procedural(tex)
            self.pg.setVisible(proc)
            self.thumb.clear()
            if proc:
                self.thumb.setText("процедур.")
                try:
                    pr = parse_procedural(tex)
                    if pr.func == "color":
                        for s, v in zip(self.pc, pr.rgba or (0, 0, 0, 0)):
                            s.setValue(v)
                        self.ptag.setText(pr.tag or "")
                    for w in self.pc + [self.ptag]:
                        w.setVisible(pr.func == "color")
                    for w in (self.fn, self.fk):
                        w.setVisible(pr.func == "fresnel")
                    if pr.func == "fresnel" and pr.fresnel:
                        self.fn.setValue(pr.fresnel[0]); self.fk.setValue(pr.fresnel[1])
                    self.info.setText(f"Функция: {pr.func}")
                except ValueError as e:
                    self.info.setText(f"Ошибка: {e}")
            else:
                f = find(tex) if tex else None
                if f:
                    arr = thumb_cache.get(f)
                    if arr is not None:
                        import numpy as np
                        rgba = np.dstack([arr[..., :3], np.full(arr.shape[:2], 255, np.uint8)]) if arr.shape[2] == 3 else arr
                        self.thumb.setPixmap(pixmap_from_rgba(rgba, 94))
                    self.info.setText(f"Файл: {f}")
                else:
                    self.thumb.setText("нет файла")
                    self.info.setText("Файл не найден в корне проекта/сопоставлениях (в игре может существовать)")
            self.src.setEditText(st.uv_source or "")
            self.filt.setEditText(st.filter or "")
            for k in UV_KEYS:
                v = st.uv(k) or [0, 0, 0]
                for s, x in zip(self.uv[k], v):
                    s.setValue(x)
            for k, x in st.uv_params().items():
                self.h[k].setValue(x)
            known = {"texture", "uvSource", "Filter"}
            extras = [n.name for n in st.node.props() if n.name not in known]
            extras += [c.name for c in st.node.classes() if c.name != "uvTransform"]
            self.extra.setText("Дополнительные (сохраняются): " + ", ".join(extras) if extras else "")
        finally:
            self._lock = False


class PropertiesPanel(QWidget):
    stage_visibility = Signal(int, bool)
    assign_requested = Signal(int)
    view_requested = Signal(int)
    target_changed = Signal(str)

    def __init__(self, proj: Project, find: Callable, thumbs):
        super().__init__()
        self.proj, self.find, self.thumbs = proj, find, thumbs
        self._lock = False
        v = QVBoxLayout(self)
        v.setContentsMargins(4, 4, 4, 4)
        top = QHBoxLayout()
        top.addWidget(QLabel("Редактируется:"))
        self.target = QComboBox()
        self.target.addItem("Общий материал (все состояния)", TARGET_SHARED)
        self.target.addItem("Декоративная ветка (все decorative_*)", TARGET_DECOR)
        for s in State:
            self.target.addItem(f"Только: {s.value}", s.value)
        self.target.currentIndexChanged.connect(lambda: (self.refresh(), self.target_changed.emit(self.target_id)))
        top.addWidget(self.target, 1)
        v.addLayout(top)
        hint = QLabel("Оранжевое название = переопределение для выбранного варианта. ↺ убирает его.")
        hint.setObjectName("hint"); hint.setWordWrap(True)
        v.addWidget(hint)
        self.tabs = QTabWidget()
        v.addWidget(self.tabs, 1)

        gen = QWidget()
        gl = QVBoxLayout(gen)
        self.colors = {}
        for name in COLOR_PARAMS:
            r = ColorRow(COLOR_LABELS[name])
            r.changed.connect(lambda vals, n=name: self.set_value(n, vals))
            r.reset.connect(lambda n=name: self.reset(n))
            self.colors[name] = r
            gl.addWidget(r)
        self.power = ParamRow("Specular Power", 0, 200, 0.5, 2)
        self.power.changed.connect(lambda x: self.set_value("specularPower", x))
        self.power.reset.connect(lambda: self.reset("specularPower"))
        gl.addWidget(self.power)
        sh = QFormLayout()
        self.ps, self.vs = QComboBox(), QComboBox()
        for c, n in ((self.ps, "PixelShaderID"), (self.vs, "VertexShaderID")):
            c.setEditable(True); c.addItems(SHADERS)
            c.activated.connect(lambda _=0, c=c, n=n: self.set_value(n, c.currentText()))
            c.lineEdit().editingFinished.connect(lambda c=c, n=n: self.set_value(n, c.currentText()))
        sh.addRow("Pixel Shader ID", self.ps)
        sh.addRow("Vertex Shader ID", self.vs)
        gl.addLayout(sh)
        self.unknown = QLabel("")
        self.unknown.setObjectName("hint"); self.unknown.setWordWrap(True)
        gl.addWidget(self.unknown)
        gl.addStretch()
        self.tabs.addTab(self._scroll(gen), "Общие")
        self.stage_eds: dict[int, StageEditor] = {}
        for i in range(1, 8):
            ed = StageEditor(self, i)
            self.stage_eds[i] = ed
            self.tabs.addTab(self._scroll(ed), f"S{i}")
        self.tabs.setTabToolTip(0, "Общие параметры материала")
        for i in range(1, 8):
            self.tabs.setTabToolTip(i, STAGE_ROLE[i])
        self.refresh()

    @staticmethod
    def _scroll(w):
        s = QScrollArea(); s.setWidgetResizable(True); s.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff); s.setWidget(w); s.setFrameShape(QScrollArea.NoFrame)
        return s

    # --- target / values
    @property
    def target_id(self) -> str:
        return self.target.currentData()

    def set_target(self, tid: str):
        i = self.target.findData(tid)
        if i >= 0:
            self.target.setCurrentIndex(i)

    def view_state(self) -> State:
        t = self.target_id
        return State.DECORATIVE if t == TARGET_DECOR else (State.BASE if t == TARGET_SHARED else State(t))

    def view_material(self):
        return self.proj.spec.shared if self.target_id == TARGET_SHARED else self.proj.effective(self.view_state())

    def current_value(self, path):
        return get_value(self.view_material(), path)

    def set_value(self, path, value, label=""):
        if self._lock:
            return
        self.proj.set_value(self.target_id, path, value, label or f"{self.target_id}: {path}")

    def reset(self, path):
        t = self.target_id
        if t == TARGET_SHARED:
            base = get_value(self.proj.baseline, path)
            if base is not None:
                self.proj.set_value(t, path, base, f"Сброс {path}")
        else:
            self.proj.reset_value(t, path)

    def refresh(self):
        self._lock = True
        try:
            m = self.view_material()
            t = self.target_id
            ov = lambda p: self.proj.is_overridden(t, p)
            for n, r in self.colors.items():
                v = m.get(n)
                if v:
                    r.set_values(v)
                r.set_overridden(ov(n))
            self.power.set_value(m.get("specularPower") or 0)
            self.power.set_overridden(ov("specularPower"))
            self.ps.setEditText(m.get("PixelShaderID") or "")
            self.vs.setEditText(m.get("VertexShaderID") or "")
            known = set(COLOR_PARAMS) | {"specularPower", "PixelShaderID", "VertexShaderID"}
            unk = [p.name for p in m.root.props() if p.name not in known]
            self.unknown.setText("Прочие параметры (сохраняются как есть): " + ", ".join(unk) if unk else "")
            for ed in self.stage_eds.values():
                ed.refresh(m, self.find, self.thumbs, ov)
        finally:
            self._lock = False

    def goto(self, location: str):
        """Переход к параметру по пути из валидатора: 'file.rvmat:Stage3.texture'."""
        loc = location.split(":", 1)[-1]
        if loc.lower().startswith("stage") and loc[5:6].isdigit():
            self.tabs.setCurrentIndex(int(loc[5]))
        else:
            self.tabs.setCurrentIndex(0)
