from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QImage, QPixmap
from PySide6.QtWidgets import (QColorDialog, QDoubleSpinBox, QHBoxLayout, QLabel, QPushButton,
                               QSlider, QToolButton, QWidget)

import numpy as np


def qimage_from_rgba(arr: np.ndarray) -> QImage:
    arr = np.ascontiguousarray(arr)
    h, w = arr.shape[:2]
    img = QImage(arr.data, w, h, w * 4, QImage.Format_RGBA8888)
    return img.copy()          # отвязать от буфера numpy


def pixmap_from_rgba(arr, size=None) -> QPixmap:
    pm = QPixmap.fromImage(qimage_from_rgba(arr))
    return pm.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation) if size else pm


class ParamRow(QWidget):
    """Название + ползунок + точное поле + мин/макс + сброс."""
    changed = Signal(float)
    reset = Signal()

    def __init__(self, label: str, lo: float, hi: float, step=0.01, decimals=3, parent=None):
        super().__init__(parent)
        self.lo, self.hi, self.step = lo, hi, step
        self.label = QLabel(label)
        self.label.setMinimumWidth(110)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, int(round((hi - lo) / step)))
        self.spin = QDoubleSpinBox()
        self.spin.setRange(lo, hi)
        self.spin.setDecimals(decimals)
        self.spin.setSingleStep(step)
        self.spin.setFixedWidth(84)
        self.spin.setToolTip(f"Диапазон: {lo} … {hi}")
        self.btn = QToolButton()
        self.btn.setText("↺")
        self.btn.setToolTip("Сбросить к исходному значению")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        for w in (self.label, self.slider, self.spin, self.btn):
            lay.addWidget(w)
        self.slider.valueChanged.connect(self._from_slider)
        self.spin.valueChanged.connect(self._from_spin)
        self.btn.clicked.connect(self.reset)

    def _from_slider(self, i):
        v = self.lo + i * self.step
        self.spin.blockSignals(True)
        self.spin.setValue(v)
        self.spin.blockSignals(False)
        self.changed.emit(v)

    def _from_spin(self, v):
        self.slider.blockSignals(True)
        self.slider.setValue(int(round((v - self.lo) / self.step)))
        self.slider.blockSignals(False)
        self.changed.emit(v)

    def set_value(self, v: float):
        for w in (self.slider, self.spin):
            w.blockSignals(True)
        self.spin.setValue(v)
        self.slider.setValue(int(round((min(max(v, self.lo), self.hi) - self.lo) / self.step)))
        for w in (self.slider, self.spin):
            w.blockSignals(False)

    def value(self) -> float:
        return self.spin.value()

    def set_overridden(self, on: bool):
        self.label.setProperty("overridden", on)
        self.label.style().unpolish(self.label)
        self.label.style().polish(self.label)


class ColorRow(QWidget):
    """RGBA: 4 поля + цветовая палитра + сброс."""
    changed = Signal(list)
    reset = Signal()

    def __init__(self, label: str, parent=None, maxv=4.0):
        super().__init__(parent)
        self.label = QLabel(label)
        self.label.setMinimumWidth(110)
        self.spins = []
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.label)
        for ch in "RGBA":
            s = QDoubleSpinBox()
            s.setRange(0, maxv)
            s.setDecimals(4)
            s.setSingleStep(0.05)
            s.setPrefix(ch + " ")
            s.valueChanged.connect(self._emit)
            self.spins.append(s)
            lay.addWidget(s)
        self.swatch = QPushButton()
        self.swatch.setFixedSize(26, 22)
        self.swatch.setToolTip("Выбрать цвет")
        self.swatch.clicked.connect(self._pick)
        self.btn = QToolButton()
        self.btn.setText("↺")
        self.btn.clicked.connect(self.reset)
        lay.addWidget(self.swatch)
        lay.addWidget(self.btn)

    def values(self) -> list:
        return [s.value() for s in self.spins]

    def _emit(self, *_):
        self._paint()
        self.changed.emit(self.values())

    def _paint(self):
        r, g, b = (max(0, min(1, v)) for v in self.values()[:3])
        self.swatch.setStyleSheet(f"background: rgb({int(r*255)},{int(g*255)},{int(b*255)}); border: 1px solid #666;")

    def _pick(self):
        r, g, b, a = self.values()
        c = QColorDialog.getColor(QColor.fromRgbF(min(r, 1), min(g, 1), min(b, 1)), self, "Цвет")
        if c.isValid():
            for s, v in zip(self.spins[:3], (c.redF(), c.greenF(), c.blueF())):
                s.blockSignals(True)
                s.setValue(round(v, 4))
                s.blockSignals(False)
            self._emit()

    def set_values(self, vals):
        for s, v in zip(self.spins, vals):
            s.blockSignals(True)
            s.setValue(v)
            s.blockSignals(False)
        self._paint()

    def set_overridden(self, on: bool):
        self.label.setProperty("overridden", on)
        self.label.style().unpolish(self.label)
        self.label.style().polish(self.label)
