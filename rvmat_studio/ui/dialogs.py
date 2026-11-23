"""Диалоги: экспорт (как в Blender), настройки, просмотр текстуры."""
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import numpy as np

from ..backend import State
from ..core import export as ex
from ..core.textures import channel_view, load_rgba, normal_report, probe
from . import theme
from .panels import np_photo
from .widgets import fmt

STATE_TITLES = {"base": "Основной (name.rvmat)", "damage": "Повреждённый (_damage)", "destruct": "Разрушенный (_destruct)",
                "decorative": "Декоративный (_decorative)", "decorative_damage": "Декоративный повреждённый", "decorative_destruct": "Декоративный разрушенный"}


def open_folder(path: str):
    try:
        if sys.platform == "win32":
            os.startfile(path)                                                # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except OSError:
        pass


class Modal(tk.Toplevel):
    def __init__(self, parent, title, width=None, height=None):
        super().__init__(parent)
        self.title(title)
        self.configure(bg=theme.BG)
        self.transient(parent.winfo_toplevel())
        if width and height:
            self.geometry(f"{width}x{height}")
        self.after(10, self._grab)

    def _grab(self):
        try:
            self.grab_set()
        except tk.TclError:
            pass


# ================================================================== экспорт
class ExportDialog(Modal):
    """Одна кнопка «Экспорт»: путь, имя меша, что экспортировать, какие RVMAT — как в диалоге экспорта Blender."""

    def __init__(self, parent, app):
        super().__init__(parent, "Экспорт", 900, 640)
        self.app = app
        p, st = app.proj, app.settings
        o = p.export_opts
        self.root_var = tk.StringVar(value=o.get("out_root") or p.project_root or st.project_root or "")
        self.mesh = tk.StringVar(value=p.mesh_name or ex.default_mesh_name(p))
        self.prefix = tk.StringVar(value=o.get("prefix") if o.get("prefix") is not None else "")
        self._prefix_manual = o.get("prefix") is not None
        self.tex = tk.BooleanVar(value=o.get("textures", True))
        self.fbx = tk.BooleanVar(value=o.get("fbx", True) and bool(p.model_path))
        self.conv = tk.StringVar(value="builtin" if (o.get("converter", "builtin") == "builtin" or not st.image_to_paa) else "tool")
        self.over = tk.BooleanVar(value=False)
        sel = set(o.get("states", ex.DEFAULT_STATES))
        self.states = {s.value: tk.BooleanVar(value=s.value in sel) for s in State}
        self._plan = None
        self._running = False
        self._q: queue.Queue = queue.Queue()
        self._after = None

        body = ttk.Frame(self); body.pack(fill="both", expand=True, padx=12, pady=10)
        left = ttk.Frame(body); left.pack(side="left", fill="y", padx=(0, 14))
        right = ttk.Frame(body); right.pack(side="left", fill="both", expand=True)

        ttk.Label(left, text="Папка проекта (корень)", style="Head.TLabel").pack(anchor="w")
        r = ttk.Frame(left); r.pack(fill="x", pady=(2, 8))
        ttk.Entry(r, textvariable=self.root_var, width=34).pack(side="left", fill="x", expand=True)
        ttk.Button(r, text="Обзор…", style="Tool.TButton", command=self.browse).pack(side="left", padx=(4, 0))
        ttk.Label(left, text="Имя меша (папка и имена файлов)", style="Head.TLabel").pack(anchor="w")
        ttk.Entry(left, textvariable=self.mesh).pack(fill="x", pady=(2, 8))
        ttk.Label(left, text="Префикс игровых путей в RVMAT", style="Head.TLabel").pack(anchor="w")
        pe = ttk.Entry(left, textvariable=self.prefix); pe.pack(fill="x", pady=(2, 0))
        pe.bind("<KeyRelease>", lambda e: setattr(self, "_prefix_manual", True))
        ttk.Label(left, text="путь корня относительно диска P:, напр. MyMod", style="Hint.TLabel").pack(anchor="w", pady=(0, 8))

        g = ttk.LabelFrame(left, text="Что экспортировать"); g.pack(fill="x", pady=4)
        ttk.Checkbutton(g, text="Текстуры (PNG/TGA → PAA)", variable=self.tex, command=self.update_plan).pack(anchor="w", padx=8, pady=2)
        cr = ttk.Frame(g); cr.pack(fill="x", padx=26)
        ttk.Label(cr, text="Конвертер:", style="Hint.TLabel").pack(side="left")
        self.convcb = ttk.Combobox(cr, state="readonly", width=24, values=["Встроенный", "DayZ Tools (ImageToPAA)"])
        self.convcb.pack(side="left", padx=4)
        self.convcb.current(0 if self.conv.get() == "builtin" else 1)
        self.convcb.bind("<<ComboboxSelected>>", lambda e: (self.conv.set("builtin" if self.convcb.current() == 0 else "tool"), self.update_plan()))
        self.fbxcb = ttk.Checkbutton(g, text="Меш FBX (в качестве модели)", variable=self.fbx, command=self.update_plan)
        self.fbxcb.pack(anchor="w", padx=8, pady=2)
        if not p.model_path:
            self.fbxcb.state(["disabled"])
        rv = ttk.LabelFrame(left, text="Какие RVMAT создавать"); rv.pack(fill="x", pady=8)
        for s in State:
            ttk.Checkbutton(rv, text=STATE_TITLES[s.value], variable=self.states[s.value], command=self.update_plan).pack(anchor="w", padx=8, pady=1)
        ttk.Label(left, text="Для каждого материала/селекшена проекта создаётся свой набор.", style="Hint.TLabel", wraplength=300).pack(anchor="w")

        ttk.Label(right, text="Будет создано", style="Head.TLabel").pack(anchor="w")
        self.treebox = tk.Text(right, height=18, wrap="none", font=("Consolas", 9)); theme.style_text(self.treebox)
        self.treebox.pack(fill="both", expand=True, pady=(2, 6))
        self.treebox.tag_configure("dir", foreground=theme.ACC2); self.treebox.tag_configure("ex", foreground=theme.WARN)
        self.treebox.tag_configure("dim", foreground=theme.DIM)
        ttk.Label(right, text="Замечания", style="Head.TLabel").pack(anchor="w")
        self.notes = tk.Text(right, height=7, wrap="word"); theme.style_text(self.notes)
        self.notes.tag_configure("ERROR", foreground=theme.ERR); self.notes.tag_configure("WARNING", foreground=theme.WARN); self.notes.tag_configure("INFO", foreground=theme.INFO)
        self.notes.pack(fill="x", pady=(2, 4))
        self.overcb = ttk.Checkbutton(right, text="Перезаписать существующие файлы (будут созданы .bak)", variable=self.over)
        self.overcb.pack(anchor="w")

        bot = ttk.Frame(self); bot.pack(fill="x", padx=12, pady=(0, 10))
        self.progress = ttk.Progressbar(bot, mode="determinate"); self.progress.pack(side="left", fill="x", expand=True)
        self.status = ttk.Label(bot, text="", style="Hint.TLabel", width=34); self.status.pack(side="left", padx=8)
        self.open_btn = ttk.Button(bot, text="Открыть папку", command=self.open_out)
        self.cancel_btn = ttk.Button(bot, text="Отмена", command=self.close); self.cancel_btn.pack(side="right", padx=(6, 0))
        self.go = ttk.Button(bot, text="Экспортировать", style="Accent.TButton", command=self.run); self.go.pack(side="right")
        for v in (self.root_var, self.mesh):
            v.trace_add("write", lambda *_: self._on_edit())
        self.prefix.trace_add("write", lambda *_: self._sched())
        self.bind("<Escape>", lambda e: self.close())
        self.protocol("WM_DELETE_WINDOW", self.close)
        if not self._prefix_manual:
            self._auto_prefix()
        self.update_plan()

    # ---- настройки -> план
    def opts(self) -> dict:
        return {"out_root": self.root_var.get().strip(), "mesh_name": self.mesh.get().strip(), "prefix": self.prefix.get(),
                "textures": self.tex.get(), "fbx": self.fbx.get(), "converter": self.conv.get(),
                "states": [k for k, v in self.states.items() if v.get()], "p_drive": self.app.settings.p_drive,
                "image_to_paa": self.app.settings.image_to_paa}

    def _auto_prefix(self):
        r = self.root_var.get().strip()
        self.prefix.set(ex.default_prefix(r, self.app.settings.p_drive) if r else "")
        self._prefix_manual = False

    def _on_edit(self):
        if not self._prefix_manual:
            self._auto_prefix()
        self._sched()

    def _sched(self):
        if self._after:
            self.after_cancel(self._after)
        self._after = self.after(250, self.update_plan)

    def browse(self):
        d = filedialog.askdirectory(parent=self, title="Папка проекта", initialdir=self.root_var.get() or self.app.settings.p_drive or None)
        if d:
            self.root_var.set(d)

    def update_plan(self):
        self._after = None
        t, n = self.treebox, self.notes
        for w in (t, n):
            w.configure(state="normal"); w.delete("1.0", "end")
        o = self.opts()
        try:
            if not o["out_root"]:
                raise ValueError("Укажите папку проекта")
            if not o["states"]:
                raise ValueError("Выберите хотя бы один RVMAT")
            if not o["mesh_name"]:
                raise ValueError("Укажите имя меша")
            self._plan = plan = ex.plan_export(self.app.proj, o)
        except Exception as e:
            self._plan = None
            n.insert("end", str(e), "ERROR")
            t.configure(state="disabled"); n.configure(state="disabled"); self.go.state(["disabled"])
            return
        self.go.state(["!disabled"] if not self._running else ["disabled"])
        out = Path(plan.out_dir)
        t.insert("end", f"{Path(o['out_root'])}\n", "dim")
        t.insert("end", f"└─ {out.name}/\n", "dir")
        fb = [x for x in plan.ops if x.kind == "fbx"]
        for x in fb:
            t.insert("end", f"   ├─ {Path(x.dst).name}   [{x.note}]\n", "ex" if Path(x.dst).exists() else "")
        t.insert("end", "   └─ data/\n", "dir")
        for x in plan.ops:
            if x.kind == "fbx":
                continue
            tag = "ex" if Path(x.dst).exists() else ""
            note = f"   [{x.note}]" if x.kind == "texture" else ""
            t.insert("end", f"        {Path(x.dst).name}{note}{'   ⚠ перезапись' if tag else ''}\n", tag)
        if plan.existing:
            self.overcb.state(["!disabled"])
        else:
            self.overcb.state(["disabled"]); self.over.set(False)
        for line in plan.notes:
            n.insert("end", "ℹ " + line + "\n", "INFO")
        for i in plan.issues[:60]:
            n.insert("end", f"[{i.code}] {i.location}: {i.message}\n", i.severity.name)
        if not plan.notes and not plan.issues:
            n.insert("end", "Замечаний нет")
        n.insert("end", "") if not plan.existing else n.insert("end", f"\nСуществующих файлов: {len(plan.existing)}", "WARNING")
        t.configure(state="disabled"); n.configure(state="disabled")
        ntex = sum(1 for x in plan.ops if x.kind == "texture")
        self.status.configure(text=f"{len(plan.ops)} файл(ов), текстур: {ntex}")

    # ---- выполнение
    def run(self):
        if self._plan is None or self._running:
            return
        if self._plan.existing and not self.over.get():
            messagebox.showwarning("Экспорт", "Некоторые файлы уже существуют. Отметьте перезапись или выберите другую папку.", parent=self)
            return
        self._running = True
        for w in (self.go,):
            w.state(["disabled"])
        plan, ow, tool = self._plan, self.over.get(), self.app.settings.image_to_paa
        self.progress.configure(maximum=len(plan.ops), value=0)

        def work():
            res = ex.run_export(plan, ow, tool, progress=lambda i, n, op: self._q.put(("p", i, n, op)))
            self._q.put(("done", res))
        threading.Thread(target=work, daemon=True).start()
        self.after(40, self._poll)

    def _poll(self):
        try:
            while True:
                m = self._q.get_nowait()
                if m[0] == "p":
                    self.progress.configure(value=m[1]); self.status.configure(text=f"{m[1] + 1}/{m[2]}: {Path(m[3].dst).name}")
                else:
                    return self._finish(m[1])
        except queue.Empty:
            pass
        self.after(40, self._poll)

    def _finish(self, res):
        self._running = False
        self.progress.configure(value=self.progress["maximum"])
        o = self.opts()
        p, st = self.app.proj, self.app.settings
        p.export_opts = {k: v for k, v in o.items() if k not in ("image_to_paa", "p_drive")}
        p.mesh_name = self.mesh.get().strip()
        if not p.project_root:
            p.project_root = o["out_root"]
            self.app.rebuild_resolver()
        st.project_root = o["out_root"]; st.save()
        p.dirty = True
        n = self.notes
        n.configure(state="normal"); n.delete("1.0", "end")
        n.insert("end", f"Готово: записано {len(res.written)} файл(ов).\n", "INFO")
        for line in res.notes:
            n.insert("end", "ℹ " + line + "\n", "INFO")
        for e in res.errors:
            n.insert("end", "✗ " + e + "\n", "ERROR")
        n.configure(state="disabled")
        self.status.configure(text=f"Записано: {len(res.written)}, ошибок: {len(res.errors)}")
        self.app.log(f"Экспорт в {self._plan.out_dir}: записано {len(res.written)}, ошибок {len(res.errors)}")
        self.app.on_project_changed()
        self.open_btn.pack(side="right", padx=6)
        self.cancel_btn.configure(text="Закрыть")
        self.last = res
        self._plan = None
        self.after(100, lambda: self.go.state(["!disabled"]))

    def open_out(self):
        open_folder(self.opts()["out_root"] and str(Path(self.opts()["out_root"]) / (self.opts()["mesh_name"] or "")))

    def close(self):
        if self._running:
            return
        self.destroy()


# ================================================================== настройки
class SettingsDialog(Modal):
    FIELDS = (("dayz_tools", "DayZ Tools (папка)", "dir"), ("workbench", "Workbench (exe)", "file"), ("image_to_paa", "ImageToPAA (exe)", "file"),
              ("gproj", "Файл проекта dayz.gproj (Workbench)", "file"), ("p_drive", "P: Drive", "dir"), ("project_root", "Корень проекта по умолчанию", "dir"))

    def __init__(self, parent, settings):
        super().__init__(parent, "Настройки и интеграция с DayZ Tools", 640, 330)
        self.s = settings
        self.vars = {}
        f = ttk.Frame(self); f.pack(fill="both", expand=True, padx=12, pady=10)
        for r, (key, label, kind) in enumerate(self.FIELDS):
            ttk.Label(f, text=label).grid(row=r, column=0, sticky="w", pady=3)
            v = tk.StringVar(value=getattr(settings, key)); self.vars[key] = v
            ttk.Entry(f, textvariable=v, width=52).grid(row=r, column=1, padx=6, sticky="ew")
            ttk.Button(f, text="…", width=3, style="Tool.TButton", command=lambda v=v, k=kind: self._browse(v, k)).grid(row=r, column=2)
        r = len(self.FIELDS)
        ttk.Label(f, text="Автосохранение, с").grid(row=r, column=0, sticky="w", pady=3)
        self.auto = tk.StringVar(value=str(settings.autosave_seconds)); ttk.Spinbox(f, from_=10, to=3600, textvariable=self.auto, width=8).grid(row=r, column=1, sticky="w", padx=6)
        ttk.Button(f, text="Найти DayZ Tools автоматически", command=self._find).grid(row=r + 1, column=1, sticky="w", padx=6, pady=8)
        f.columnconfigure(1, weight=1)
        b = ttk.Frame(self); b.pack(fill="x", padx=12, pady=(0, 10))
        ttk.Button(b, text="Отмена", command=self.destroy).pack(side="right", padx=(6, 0))
        ttk.Button(b, text="OK", style="Accent.TButton", command=self._ok).pack(side="right")
        self.ok = False

    def _browse(self, v, kind):
        x = filedialog.askdirectory(parent=self) if kind == "dir" else filedialog.askopenfilename(parent=self)
        if x:
            v.set(x)

    def _find(self):
        if self.s.find_tools():
            for k, v in self.vars.items():
                v.set(getattr(self.s, k))
        else:
            messagebox.showinfo("DayZ Tools", "Не найдено в стандартных папках Steam. Укажите путь вручную.", parent=self)

    def _ok(self):
        for k, v in self.vars.items():
            setattr(self.s, k, v.get().strip())
        try:
            self.s.autosave_seconds = max(10, int(self.auto.get()))
        except ValueError:
            pass
        self.ok = True
        self.destroy()


# ================================================================== просмотр текстуры
class TextureViewer(Modal):
    MODES = (("Цвет (RGB)", "rgb"), ("С альфой", "rgba"), ("R", "r"), ("G", "g"), ("B", "b"), ("Alpha", "a"), ("Normal: инверсия G", "swapg"))

    def __init__(self, parent, path):
        super().__init__(parent, f"Просмотр — {Path(path).name}", 860, 700)
        self.path = path
        top = ttk.Frame(self); top.pack(fill="x", padx=8, pady=6)
        self.mode = tk.StringVar(value="rgb")
        for t, m in self.MODES:
            ttk.Radiobutton(top, text=t, value=m, variable=self.mode, command=self.draw).pack(side="left", padx=4)
        self.canvas = tk.Canvas(self, bg="#111", highlightthickness=0); self.canvas.pack(fill="both", expand=True, padx=8)
        self.info = tk.Text(self, height=6, wrap="word"); theme.style_text(self.info); self.info.pack(fill="x", padx=8, pady=8)
        self.bind("<Configure>", lambda e: self.draw())
        self._photo = None
        try:
            self.arr, self.fmt = load_rgba(path)
        except Exception as e:
            self.arr = None
            self.info.insert("1.0", f"Ошибка: {e}")
            return
        self._info()
        self.after(30, self.draw)

    def _info(self):
        ti = probe(self.path)
        lines = [str(ti.path), f"Разрешение: {ti.width}×{ti.height} · формат {ti.fmt} · альфа: {'да' if ti.has_alpha else 'нет'}"] + [f"⚠ {x}" for x in ti.problems()]
        n = normal_report(self.arr)
        lines.append(f"Проверка нормалей (только для *_nohq): средняя длина {n['mean_len']:.2f}, доля Z<0: {n['z_negative_ratio']:.1%}")
        if self.path.lower().endswith(".paa") and "_no" in Path(self.path).stem.lower():
            lines.append("ℹ _nohq из PAA: применена обратная перестановка каналов (X нормали хранится инвертированным в альфе).")
        self.info.insert("1.0", "\n".join(lines))

    def draw(self):
        if self.arr is None:
            return
        view = self.arr if self.mode.get() == "rgba" else channel_view(self.arr, self.mode.get())
        W, H = max(self.canvas.winfo_width(), 100), max(self.canvas.winfo_height(), 100)
        h, w = view.shape[:2]
        s = min(W / w, H / h, 8.0)
        from PIL import Image
        im = Image.fromarray(np.ascontiguousarray(view)).convert("RGB").resize((max(1, int(w * s)), max(1, int(h * s))), Image.NEAREST if s > 1 else Image.BILINEAR)
        self._photo = np_photo(np.asarray(im))
        self.canvas.delete("all")
        self.canvas.create_image(W // 2, H // 2, image=self._photo)
