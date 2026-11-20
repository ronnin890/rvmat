"""Интерактивное окно предпросмотра: вращение (ЛКМ), панорама (ПКМ/СКМ), зум (колесо)."""
from __future__ import annotations

import time

import numpy as np
from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Qt, Signal
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QWidget
from PySide6.QtOpenGLWidgets import QOpenGLWidget

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


class _CameraMixin:
    """Мышь/колесо/сброс камеры — общие для CPU- и GL-виджетов."""
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

    def reset_camera(self):
        self.opts.yaw, self.opts.pitch, self.opts.zoom, self.opts.pan = 25.0, -15.0, 1.0, (0.0, 0.0)
        self.request()


class PreviewWidget(_CameraMixin, QWidget):
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

    def has_image(self) -> bool:
        return self._img is not None

    def is_idle(self) -> bool:
        return not self._busy and not self._pending

    backend = "cpu"

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

    def shutdown(self):
        self._timer.stop(); self._final_timer.stop()
        self.mat_a = None
        self.pool.waitForDone(5000)



# ====================================================================== OpenGL
_offscreen = None


def offscreen_gl():
    global _offscreen
    if _offscreen is None:
        from ..core.gl_render import GLOffscreen
        _offscreen = GLOffscreen()
    return _offscreen


def close_offscreen():
    """Освободить офлайн-контекст GL до выхода из QApplication (иначе утечка/падение при завершении)."""
    global _offscreen
    if _offscreen is not None:
        try:
            _offscreen.close()
        finally:
            _offscreen = None


class GLPreviewWidget(_CameraMixin, QOpenGLWidget):
    """Предпросмотр на видеокарте (GLSL 3.30). Тот же интерфейс, что у PreviewWidget."""
    status = Signal(str)
    gl_failed = Signal(str)
    backend = "gl"

    def __init__(self, parent=None):
        super().__init__(parent)
        from ..core.gl_render import surface_format
        self.setFormat(surface_format())
        self.setMinimumSize(320, 320)
        self.opts = RenderOptions()
        self.mat_a = self.mat_b = None
        self.resolver = None
        self.mesh = None
        self.hdri = None
        self.show_uv = False
        self.uv_bg = None
        self.scene = None
        self._painted = False
        self._uv_cache = None
        self._drag = None

    # --- GL
    def initializeGL(self):
        from ..core.gl_render import GLScene
        try:
            self.scene = GLScene(self.context())
        except Exception as e:
            self.scene = None
            self.gl_failed.emit(str(e))

    def _mesh(self):
        return self.mesh if (self.mesh is not None and self.opts.shape == "Своя модель") else get_shape(self.opts.shape)

    def paintGL(self):
        if self.scene is None:
            return
        dpr = self.devicePixelRatioF()
        W, H = max(int(self.width() * dpr), 1), max(int(self.height() * dpr), 1)
        f = self.context().functions()
        f.glViewport(0, 0, W, H)
        f.glClearColor(0.05, 0.05, 0.06, 1.0)
        f.glClear(0x4000 | 0x0100)
        if self.mat_a is None or self.resolver is None:
            return
        try:
            if self.show_uv:
                self._paint_uv()
                return
            o = self.opts
            if self.mat_b is not None:
                half = W // 2
                self.scene.draw(self.mat_a, o, self.resolver, self._mesh(), (0, 0, half, H), self.hdri)
                self.scene.draw(self.mat_b, o, self.resolver, self._mesh(), (half, 0, W - half, H), self.hdri)
            else:
                self.scene.draw(self.mat_a, o, self.resolver, self._mesh(), (0, 0, W, H), self.hdri)
            self._painted = True
        except Exception as e:
            self.status.emit(f"Ошибка OpenGL-рендера: {e}")
            self.gl_failed.emit(str(e))

    def _paint_uv(self):
        key = (id(self._mesh()), id(self.uv_bg))
        if self._uv_cache is None or self._uv_cache[0] != key:
            self._uv_cache = (key, qimage_from_rgba(render_uv_layout(self._mesh(), 512, self.uv_bg)))
        p = QPainter(self)
        side = min(self.width(), self.height())
        p.drawImage(self.rect().center().x() - side // 2, self.rect().center().y() - side // 2, self._uv_cache[1].scaled(side, side))
        p.end()
        self._painted = True

    # --- API (как у PreviewWidget)
    def set_materials(self, a, b=None, resolver=None):
        self.mat_a, self.mat_b = a, b
        if resolver:
            self.resolver = resolver
        self.update()

    def request(self, interactive=False):
        self.update()

    def has_image(self) -> bool:
        return self._painted

    def is_idle(self) -> bool:
        return True

    def render_sync(self) -> np.ndarray:
        o = RenderOptions(**{k: getattr(self.opts, k) for k in self.opts.__dataclass_fields__})
        o.stages_on = dict(self.opts.stages_on)
        o.width, o.height = max(self.width(), 64), max(self.height(), 64)
        return offscreen_gl().render(self.mat_a, o, self.resolver, self._mesh(), self.hdri)

    def shutdown(self):
        try:
            if self.scene is not None:
                self.makeCurrent()
                self.scene.destroy()
                self.scene = None
                self.doneCurrent()
        except Exception:
            pass
