"""Окно предпросмотра: программный рендер (numpy) в отдельном потоке, вращение/сдвиг/масштаб мышью, выбор частей кликом."""
import copy
import queue
import threading
import tkinter as tk
from tkinter import ttk

import numpy as np

from ..core.meshes import get_shape
import os

from ..core import glrender
from ..core.pick import pick_part_ray
from ..core.render import RenderCache, RenderOptions, render, render_uv_layout


try:
    from PIL import Image, ImageTk
except ImportError:                                           # без ImageTk — PPM через Tcl (медленнее)
    ImageTk = None


def to_photo(rgba: np.ndarray, zoom: int = 1) -> tk.PhotoImage:
    h, w = rgba.shape[:2]
    if ImageTk is not None:
        p = ImageTk.PhotoImage(Image.fromarray(np.ascontiguousarray(rgba[:, :, :3]), "RGB"))
    else:
        p = tk.PhotoImage(data=b"P6 %d %d 255\n" % (w, h) + np.ascontiguousarray(rgba[:, :, :3]).tobytes())
    return p.zoom(zoom) if zoom > 1 else p


class PreviewView(ttk.Frame):
    HINT = "ЛКМ — вращение · ПКМ — сдвиг · колесо — масштаб · клик по модели — выбор части · приближённый рендер, не движок DayZ"

    def __init__(self, parent, on_pick=None, on_status=None, renderer="auto"):
        super().__init__(parent)
        self.on_pick, self.on_status = on_pick, on_status
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
        self.canvas = tk.Canvas(self, bg="#000", highlightthickness=0, cursor="fleur")
        self.canvas.pack(fill="both", expand=True)
        self._item = self.canvas.create_image(0, 0, anchor="nw")
        self._hint = self.canvas.create_text(8, 0, anchor="sw", fill="#9aa", font=("Segoe UI", 8), text=self.HINT)
        self.opts = RenderOptions()
        self.mat_a = self.mat_b = None
        self.parts_a = self.parts_b = None
        self.resolver = None
        self.mesh = None
        self.shape = "Сфера"
        self.show_uv = False
        self.uv_bg = None
        self.hdri = None
        self._photo = None
        self._cache = RenderCache()
        self._q: queue.Queue = queue.Queue()
        self._gen = 0
        self._busy = self._pending = False
        self._quality = self._interacting = False
        self._drag = None
        self._after_kick = self._after_final = None
        self._image_ready = False
        c = self.canvas
        c.bind("<Configure>", lambda e: (self.canvas.coords(self._hint, 8, e.height - 6), self.request()))
        c.bind("<ButtonPress-1>", self._press)
        c.bind("<ButtonPress-3>", self._press)
        c.bind("<ButtonPress-2>", self._press)
        c.bind("<B1-Motion>", self._move)
        c.bind("<B3-Motion>", self._move)
        c.bind("<B2-Motion>", self._move)
        c.bind("<ButtonRelease-1>", self._release)
        c.bind("<ButtonRelease-3>", self._release)
        c.bind("<ButtonRelease-2>", self._release)
        c.bind("<MouseWheel>", lambda e: self._zoom(1.1 if e.delta > 0 else 1 / 1.1))
        c.bind("<Button-4>", lambda e: self._zoom(1.1))
        c.bind("<Button-5>", lambda e: self._zoom(1 / 1.1))
        self.after(15, self._poll)

    # ---- API
    def current_mesh(self):
        return self.mesh if (self.mesh is not None and self.shape == "Своя модель") else get_shape(self.shape)

    def set_materials(self, a, b=None, resolver=None, parts_a=None, parts_b=None):
        self.mat_a, self.mat_b, self.parts_a, self.parts_b = a, b, parts_a, parts_b
        if resolver:
            self.resolver = resolver
        self.request()

    def has_image(self) -> bool:
        return self._image_ready

    def is_idle(self) -> bool:
        return not self._busy and not self._pending and self._after_kick is None

    def reset_camera(self):
        self.opts.yaw, self.opts.pitch, self.opts.zoom, self.opts.pan = 25.0, -15.0, 1.0, (0.0, 0.0)
        self.request()

    def shutdown(self):
        for a in (self._after_kick, self._after_final):
            if a:
                try:
                    self.after_cancel(a)
                except Exception:
                    pass
        self.mat_a = None
        if self._gl is not None:
            self._gl.stop()
            self._gl = None

    def request(self, interactive=False):
        """Сначала быстрый кадр (1×, при перетаскивании — половинное разрешение), через паузу — качественный (2×)."""
        self._interacting, self._quality = interactive, False
        if self._after_kick is None:
            self._after_kick = self.after(20, self._kick)
        if self._after_final:
            self.after_cancel(self._after_final)
        self._after_final = self.after(350, self._final)

    def _final(self):
        self._after_final = None
        self._interacting, self._quality = False, True
        self._kick()

    # ---- рендер
    def _size(self):
        return max(self.canvas.winfo_width(), 64), max(self.canvas.winfo_height(), 64)

    def _kick(self):
        self._after_kick = None
        if self.mat_a is None or self.resolver is None:
            return
        if self._busy:
            self._pending = True
            return
        self._busy = True
        self._gen += 1
        gen = self._gen
        W, H = self._size()
        o = copy.copy(self.opts)
        o.stages_on = dict(self.opts.stages_on)
        o.hdri = self.hdri
        cmp = self.mat_b is not None
        w = max(W // (2 if cmp else 1), 64)
        gl = self.backend == "gl"
        fast = self._interacting and not gl                      # на GPU всегда полное разрешение (MSAA 4×)
        scale = 0.5 if fast else 1.0
        o.width, o.height = max(int(w * scale), 32), max(int(H * scale), 32)
        o.ssaa = 2 if (self._quality and not fast and not gl) else 1
        a, b, res, mesh = self.mat_a, self.mat_b, self.resolver, self.current_mesh()
        pa, pb, cache, show_uv, uv_bg = self.parts_a, self.parts_b, self._cache, self.show_uv, self.uv_bg

        find = self.resolver.find if hasattr(self.resolver, "find") else (lambda t: None)

        def compose(renderer_fn):
            img = renderer_fn(a, pa)
            if b is not None:
                img = np.concatenate([img, renderer_fn(b, pb)], axis=1)
            return img

        if gl:
            def job(r):
                if show_uv:
                    return render_uv_layout(mesh, 512, uv_bg)
                return compose(lambda m, parts: r.render(m, o, find, mesh, parts))

            def done(box):
                self._q.put((gen, box.get("result"), 1, box.get("error")))
            self._gl.submit(job, done)
            return

        def work():
            try:
                if show_uv:
                    img = render_uv_layout(mesh, 512, uv_bg)
                else:
                    img = compose(lambda m, parts: render(m, o, res, mesh, cache, parts))
                self._q.put((gen, img, 2 if fast else 1, None))
            except Exception as e:                       # ошибка рендера не должна ронять окно
                self._q.put((gen, None, 1, e))
        threading.Thread(target=work, daemon=True).start()

    def _poll(self):
        try:
            while True:
                gen, img, zoom, err = self._q.get_nowait()
                self._busy = False
                if err is not None:
                    if self.on_status:
                        self.on_status(f"Ошибка предпросмотра: {err}")
                elif gen == self._gen:
                    self._show(img, zoom)
                if self._pending:
                    self._pending = False
                    self._kick()
        except queue.Empty:
            pass
        try:
            self.after(12, self._poll)
        except tk.TclError:
            pass

    def _show(self, img, zoom):
        W, H = self._size()
        ph = to_photo(img, zoom)
        self._photo = ph
        self.canvas.itemconfigure(self._item, image=ph)
        self.canvas.coords(self._item, max((W - ph.width()) // 2, 0), max((H - ph.height()) // 2, 0))
        self._image_ready = True

    def render_sync(self) -> np.ndarray:
        o = copy.copy(self.opts)
        o.stages_on = dict(self.opts.stages_on)
        o.hdri = self.hdri
        o.width, o.height = self._size()
        if self.backend == "gl":
            find, mesh, a, pa = self.resolver.find, self.current_mesh(), self.mat_a, self.parts_a
            return self._gl.call(lambda r: r.render(a, o, find, mesh, pa))
        return render(self.mat_a, o, self.resolver, self.current_mesh(), RenderCache(), self.parts_a)

    # ---- мышь
    def _press(self, e):
        self._drag = (e.x, e.y, e.num, self.opts.yaw, self.opts.pitch, self.opts.pan)

    def _move(self, e):
        if not self._drag:
            return
        x0, y0, btn, yaw, pitch, pan = self._drag
        dx, dy = e.x - x0, e.y - y0
        if btn == 1:
            self.opts.yaw = yaw + dx * 0.5
            self.opts.pitch = max(-89, min(89, pitch + dy * 0.5))
        else:
            k = 2.0 / max(self.canvas.winfo_height(), 1) / self.opts.zoom
            self.opts.pan = (pan[0] - dx * k, pan[1] + dy * k)
        self.request(True)

    def _release(self, e):
        d, self._drag = self._drag, None
        if d and d[2] == 1 and abs(e.x - d[0]) + abs(e.y - d[1]) < 4:
            self.pick_at(e.x, e.y)
        self.request()

    def _zoom(self, f):
        self.opts.zoom = max(0.2, min(6, self.opts.zoom * f))
        self.request(True)

    def pick_at(self, x, y) -> int:
        """Часть (селекшен) под курсором: индекс или -1. Вызывает on_pick."""
        mesh = self.current_mesh()
        if mesh.face_part is None:
            return -1
        W, H = self._size()
        if self.mat_b is not None:
            W = W // 2
            x = x % max(W, 1)
        part = pick_part_ray(mesh, copy.copy(self.opts), W, H, x, y)
        if self.on_pick:
            self.on_pick(part)
        return part
