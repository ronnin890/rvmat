"""Окно предпросмотра (Qt): рендер вне GUI-потока — на видеокарте (moderngl, свой поток с контекстом) или на процессоре (numpy).

Вращение — ЛКМ, сдвиг — ПКМ/СКМ, масштаб — колесо, клик по модели — выбор части (селекшена)."""
from __future__ import annotations

import copy
import os
import threading

import numpy as np
from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QWidget

from ..core import glrender
from ..core.logs import log_exc
from ..core.meshes import Mesh, get_shape
from ..core.pick import pick_part_ray
from ..core.render import RenderCache, RenderOptions, render, render_uv_layout
from .widgets import qimage_from_rgba

HINT = "ЛКМ — вращение · ПКМ — сдвиг · колесо — масштаб · клик по модели — выбор части · приближённый рендер, не движок DayZ"


class PreviewWidget(QWidget):
    status = Signal(str)
    part_picked = Signal(int)
    _done = Signal(int, object, int, object)             # gen, картинка, масштаб, ошибка (из рабочих потоков)

    def __init__(self, parent=None, renderer: str = "auto"):
        super().__init__(parent)
        self.setMinimumSize(320, 320)
        self.setMouseTracking(True)
        self.backend, self.backend_info, self._gl = "cpu", "программный рендер (процессор)", None
        mode = os.environ.get("RVMAT_RENDERER") or renderer
        if mode in ("auto", "gl") and glrender.available():
            g = glrender.GLThread()
            g.ready.wait(15)
            if g.renderer is not None:
                self._gl, self.backend, self.backend_info = g, "gl", f"GPU: {g.renderer.info}"
            else:
                self.backend_info = f"программный рендер (GPU недоступен: {g.error})"
        elif mode in ("auto", "gl"):
            self.backend_info = "программный рендер (moderngl не установлен)"
        self.opts = RenderOptions()
        self.mat_a = self.mat_b = None
        self.parts_a = self.parts_b = None               # {индекс части: (Material, base_color, normal_strength)}
        self.resolver = None
        self.mesh: Mesh | None = None                    # пользовательская модель (OBJ/FBX)
        self.hdri = None
        self.show_uv = False
        self.uv_bg = None
        self._img = None
        self._img_zoom = 1
        self._cache = RenderCache()
        self._gen = 0
        self._busy = self._pending = False
        self._quality = self._interacting = False
        self._drag = None
        self._closed = False
        self._worker: threading.Thread | None = None
        self._timer = QTimer(self, singleShot=True, interval=20, timeout=self._kick)
        self._final_timer = QTimer(self, singleShot=True, interval=350, timeout=self._final)
        self._done.connect(self._on_done)

    # ---- API
    def _mesh(self):
        return self.mesh if (self.mesh is not None and self.opts.shape == "Своя модель") else get_shape(self.opts.shape)

    def set_materials(self, a, b=None, resolver=None, parts_a=None, parts_b=None):
        self.mat_a, self.mat_b, self.parts_a, self.parts_b = a, b, parts_a, parts_b
        if resolver:
            self.resolver = resolver
        self.request()

    def request(self, interactive=False):
        """Сначала быстрый кадр (при перетаскивании — половинное разрешение на CPU), через паузу — качественный."""
        self._interacting, self._quality = interactive, False
        if not self._timer.isActive():
            self._timer.start()
        self._final_timer.start()

    def _final(self):
        self._interacting, self._quality = False, True
        self._kick()

    def has_image(self) -> bool:
        return self._img is not None

    def is_idle(self) -> bool:
        return not self._busy and not self._pending and not self._timer.isActive()

    def reset_camera(self):
        self.opts.yaw, self.opts.pitch, self.opts.zoom, self.opts.pan = 25.0, -15.0, 1.0, (0.0, 0.0)
        self.request()

    def shutdown(self):
        """Остановить таймеры и дождаться рабочих потоков: иначе поток пошлёт сигнал уже уничтоженному виджету (сбой при выходе)."""
        self._closed = True
        self._timer.stop()
        self._final_timer.stop()
        self.mat_a = None
        w, self._worker = self._worker, None
        if w is not None and w.is_alive():
            w.join(15)
        if self._gl is not None:
            self._gl.stop()
            self._gl = None

    # ---- рендер
    def _size(self):
        return max(self.width(), 64), max(self.height(), 64)

    def _opts_copy(self) -> RenderOptions:
        o = copy.copy(self.opts)
        o.stages_on = dict(self.opts.stages_on)
        o.hdri = self.hdri
        return o

    def _kick(self):
        if self.mat_a is None or self.resolver is None:
            return
        if self._busy:
            self._pending = True
            return
        self._busy = True
        self._gen += 1
        gen = self._gen
        W, H = self._size()
        o = self._opts_copy()
        cmp = self.mat_b is not None
        w = max(W // (2 if cmp else 1), 64)
        gl = self.backend == "gl"
        fast = self._interacting and not gl                      # на GPU всегда полное разрешение (MSAA 4×)
        scale = 0.5 if fast else 1.0
        o.width, o.height = max(int(w * scale), 32), max(int(H * scale), 32)
        o.ssaa = 2 if (self._quality and not fast and not gl) else 1
        a, b, res, mesh = self.mat_a, self.mat_b, self.resolver, self._mesh()
        pa, pb, cache, show_uv, uv_bg = self.parts_a, self.parts_b, self._cache, self.show_uv, self.uv_bg
        find = res.find if hasattr(res, "find") else (lambda t: None)

        def compose(fn):
            img = fn(a, pa)
            return np.concatenate([img, fn(b, pb)], axis=1) if b is not None else img

        if gl:
            def job(r):
                if show_uv:
                    return render_uv_layout(mesh, 512, uv_bg)
                return compose(lambda m, parts: r.render(m, o, find, mesh, parts))
            self._gl.submit(job, lambda box: None if self._closed else self._done.emit(gen, box.get("result"), 1, box.get("error")))
            return

        def work():
            try:
                img = render_uv_layout(mesh, 512, uv_bg) if show_uv else compose(lambda m, parts: render(m, o, res, mesh, cache, parts))
                if not self._closed:
                    self._done.emit(gen, img, 2 if fast else 1, None)
            except Exception as e:                               # ошибка рендера не должна ронять окно
                log_exc("gui.preview.work")
                if not self._closed:
                    self._done.emit(gen, None, 1, e)
        self._worker = threading.Thread(target=work, daemon=True)
        self._worker.start()

    def _on_done(self, gen, img, zoom, err):
        if self._closed:
            return
        self._busy = False
        if err is not None:
            self.status.emit(f"Ошибка предпросмотра: {err}")
        elif gen == self._gen and img is not None:
            self._img, self._img_zoom = qimage_from_rgba(np.ascontiguousarray(img)), zoom
            self.update()
        if self._pending:
            self._pending = False
            self._kick()

    def render_sync(self) -> np.ndarray:
        """Синхронный рендер (скриншоты, тесты)."""
        o = self._opts_copy()
        o.width, o.height = self._size()
        if self.backend == "gl":
            find, mesh, a, pa = self.resolver.find, self._mesh(), self.mat_a, self.parts_a
            return self._gl.call(lambda r: r.render(a, o, find, mesh, pa))
        return render(self.mat_a, o, self.resolver, self._mesh(), RenderCache(), self.parts_a)

    # ---- события
    def paintEvent(self, _):
        p = QPainter(self)
        p.fillRect(self.rect(), Qt.black)
        if self._img is not None:
            sc = self._img.scaled(self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            p.drawImage((self.width() - sc.width()) // 2, (self.height() - sc.height()) // 2, sc)
        p.setPen(Qt.gray)
        p.drawText(8, self.height() - 8, HINT)

    def resizeEvent(self, e):
        self.request()

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
        d, self._drag = self._drag, None
        if d is not None and d[1] == Qt.LeftButton and (e.position() - d[0]).manhattanLength() < 4:
            self.pick_at(e.position().x(), e.position().y())      # клик без перетаскивания — выбор части
        self.request()

    def wheelEvent(self, e):
        self.opts.zoom = max(0.2, min(6, self.opts.zoom * (1.1 if e.angleDelta().y() > 0 else 1 / 1.1)))
        self.request(True)

    def pick_at(self, x: float, y: float) -> int:
        """Часть (селекшен) под курсором: индекс или -1. Испускает part_picked."""
        mesh = self._mesh()
        if mesh.face_part is None:
            return -1
        W, H = self._size()
        if self.mat_b is not None:                              # сравнение: две половины одной сцены
            W = W // 2
            x = x % max(W, 1)
        part = pick_part_ray(mesh, copy.copy(self.opts), W, H, x, y)
        self.part_picked.emit(part)
        return part
