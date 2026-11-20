"""Интерактивное окно предпросмотра: вращение (ЛКМ), панорама (ПКМ/СКМ), зум (колесо)."""
from __future__ import annotations

import time

import numpy as np
from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Qt, Signal
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QWidget

from ..core.meshes import SHAPES, Mesh
from ..core.meshes import get_shape
from ..core.render import RenderCache, RenderOptions, render, render_uv_layout
from .widgets import qimage_from_rgba


class _Signals(QObject):
    done = Signal(int, object)


class _Job(QRunnable):
    def __init__(self, gen, fn, sig):
        super().__init__()
        self.gen, self.fn, self.s = gen, fn, sig
        self.setAutoDelete(False)

    def run(self):
        try:
            res = self.fn()
        except Exception as e:       # не роняем GUI из-за ошибки рендера
            res = e
        try:
            self.s.done.emit(self.gen, res)
        except RuntimeError:      # окно уже закрыто
            pass


class PreviewWidget(QWidget):
    status = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(320, 320)
        self.opts = RenderOptions()
        self.mat_a = self.mat_b = None
        self.resolver = None
        self.mesh: Mesh | None = None        # пользовательская модель (OBJ)
        self.hdri = None
        self.show_uv = False
        self.uv_bg = None
        self._img = None
        self._gen = 0
        self._busy = False
        self._pending = False
        self._drag = None
        self._interacting = False
        self.pool = QThreadPool.globalInstance()
        self._timer = QTimer(self, singleShot=True, interval=25, timeout=self._kick)
        self._final_timer = QTimer(self, singleShot=True, interval=350, timeout=self._final)
        self._jobs = []
        self._cache = RenderCache()
        self._quality = False
        self._sig = _Signals(self)
        self._sig.done.connect(self._on_done)
        self.setMouseTracking(True)

    # --- API
    def set_materials(self, a, b=None, resolver=None):
        self.mat_a, self.mat_b = a, b
        if resolver:
            self.resolver = resolver
        self.request()

    def request(self, interactive=False):
        """Сначала быстрый кадр (1×, при перетаскивании — половинное разрешение), через паузу — качественный (2×)."""
        self._interacting = interactive
        self._quality = False
        self._timer.start()
        self._final_timer.start()

    def _final(self):
        self._interacting = False
        self._quality = True
        self._kick()

    def _kick(self):
        if self.mat_a is None or self.resolver is None:
            return
        if self._busy:
            self._pending = True
            return
        self._busy = True
        self._gen += 1
        o = self._opts_copy()
        compare = self.mat_b is not None
        w = max(self.width() // (2 if compare else 1), 64)
        h = max(self.height(), 64)
        fast = self._interacting
        scale = 0.5 if fast else 1.0
        o.width, o.height = max(int(w * scale), 64), max(int(h * scale), 64)
        o.ssaa = 2 if (self._quality and not fast) else 1
        cache = self._cache
        a, b, res, mesh, show_uv = self.mat_a, self.mat_b, self.resolver, self._mesh(), self.show_uv
        uv_bg = self.uv_bg
        side = min(o.width, o.height)

        def work():
            if show_uv:
                return render_uv_layout(mesh, 512, uv_bg)
            ia = render(a, o, res, mesh, cache)
            if b is not None:
                ib = render(b, o, res, mesh, cache)
                return np.concatenate([ia, ib], axis=1)
            return ia
        job = _Job(self._gen, work, self._sig)
        self._jobs.append(job)
        self.pool.start(job)

    def _opts_copy(self) -> RenderOptions:
        import copy
        o = copy.copy(self.opts)
        o.stages_on = dict(self.opts.stages_on)
        o.hdri = self.hdri            # type: ignore[attr-defined]
        return o

    def _mesh(self):
        return self.mesh if (self.mesh is not None and self.opts.shape == "Своя модель") else get_shape(self.opts.shape)

    def _on_done(self, gen, res):
        self._busy = False
        self._jobs = [j for j in self._jobs if j.gen != gen]
        if isinstance(res, Exception):
            self.status.emit(f"Ошибка предпросмотра: {res}")
        else:
            self._img = qimage_from_rgba(res)
            self.update()
        if self._pending:
            self._pending = False
            self._kick()

    def render_sync(self) -> np.ndarray:
        """Синхронный рендер (для тестов/скриншотов)."""
        o = self._opts_copy()
        o.width, o.height = max(self.width(), 64), max(self.height(), 64)
        return render(self.mat_a, o, self.resolver, self._mesh(), RenderCache())

    # --- события
    def paintEvent(self, _):
        p = QPainter(self)
        p.fillRect(self.rect(), Qt.black)
        if self._img is not None:
            sc = self._img.scaled(self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            p.drawImage((self.width() - sc.width()) // 2, (self.height() - sc.height()) // 2, sc)
        p.setPen(Qt.gray)
        p.drawText(8, self.height() - 8, "ЛКМ — вращение · ПКМ — сдвиг · колесо — масштаб · приближённый рендер, не движок DayZ")

    def mousePressEvent(self, e):
        self._drag = (e.position(), e.button(), self.opts.yaw, self.opts.pitch, self.opts.pan)

    def mouseMoveEvent(self, e):
        if not self._drag:
            return
        p0, btn, yaw, pitch, pan = self._drag
        d = e.position() - p0
        if btn == Qt.LeftButton:
            self.opts.yaw = yaw + d.x() * 0.5
            self.opts.pitch = max(-89, min(89, pitch + d.y() * 0.5))
        else:
            k = 2.0 / max(self.height(), 1) / self.opts.zoom
            self.opts.pan = (pan[0] - d.x() * k, pan[1] + d.y() * k)
        self.request(True)

    def mouseReleaseEvent(self, e):
        self._drag = None
        self.request()

    def wheelEvent(self, e):
        self.opts.zoom = max(0.2, min(6, self.opts.zoom * (1.1 if e.angleDelta().y() > 0 else 1 / 1.1)))
        self.request(True)

    def resizeEvent(self, e):
        self.request()

    def shutdown(self):
        self._timer.stop(); self._final_timer.stop()
        self.mat_a = None
        self.pool.waitForDone(5000)

    def reset_camera(self):
        self.opts.yaw, self.opts.pitch, self.opts.zoom, self.opts.pan = 25.0, -15.0, 1.0, (0.0, 0.0)
        self.request()
