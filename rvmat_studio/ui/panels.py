"""Левая, правая и нижняя панели."""
import math
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import numpy as np

from ..core.logs import log_exc
from ..backend import ALL_STATES, Severity, State, kit_filename
from ..backend.procedural import is_procedural, make_color, make_fresnel, parse_procedural
from ..core import simple as S
from ..core.presets import CATEGORIES
from ..core.project import TARGET_DECOR, TARGET_SHARED
from ..core.textures import ALL_EXT, STAGE_ROLE, auto_match, guess_stage, probe, scan_folder
from . import theme
from .preview import to_photo
from .widgets import ColorRow, ScrollFrame, SliderRow, ask_text, fmt, tree

COLOR_LABELS = {"ambient": "Ambient", "diffuse": "Diffuse", "forcedDiffuse": "Forced Diffuse", "emmisive": "Emmisive", "specular": "Specular"}
SHADERS = ["Super", "Normal", "NormalMap", "Multi", "Glass", "Water", "Terrain", "Detail"]
UV_SOURCES = ["tex", "tex1", "none"]
FILTERS = ["", "Trilinear", "Anisotropic", "Linear", "Point"]
SEV = {"ERROR": "Критическая", "WARNING": "Предупреждение", "INFO": "Инфо"}
IMG_TYPES = [("Текстуры", " ".join("*" + e for e in sorted(ALL_EXT))), ("Все файлы", "*.*")]


def np_photo(arr: np.ndarray) -> tk.PhotoImage:
    a = arr if arr.shape[2] >= 3 else np.dstack([arr] * 3)
    return to_photo(np.dstack([a[..., :3], np.full(a.shape[:2], 255, np.uint8)]))


# ================================================================== левая панель
class LeftPanel(ttk.Notebook):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.tex, self.presets, self.states, self.parts = TexturesTab(self, app), PresetsTab(self, app), StatesTab(self, app), PartsTab(self, app)
        for w, t in ((self.tex, "Текстуры"), (self.presets, "Пресеты"), (self.states, "Состояния"), (self.parts, "Части")):
            self.add(w, text=t)

    def refresh(self):
        self.tex.refresh(); self.states.refresh(); self.parts.refresh()


class TexturesTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self._photos: dict = {}
        bar = ttk.Frame(self); bar.pack(fill="x", padx=6, pady=(6, 3))
        for t, c in (("+ Файлы", self.add_files), ("+ Папка", self.add_folder), ("Авто-назначение", self.auto_assign)):
            ttk.Button(bar, text=t, command=c, style="Tool.TButton").pack(side="left", padx=(0, 4))
        self.q = tk.StringVar(); self.q.trace_add("write", lambda *_: self.refresh())
        ttk.Entry(self, textvariable=self.q).pack(fill="x", padx=6, pady=2)
        st = ttk.Style(); st.configure("Thumb.Treeview", rowheight=50)
        f = ttk.Frame(self); f.pack(fill="both", expand=True, padx=6, pady=3)
        self.tv = ttk.Treeview(f, columns=("stage",), show="tree headings", style="Thumb.Treeview", selectmode="extended")
        self.tv.heading("#0", text="Текстура"); self.tv.heading("stage", text="по имени")
        self.tv.column("#0", width=210); self.tv.column("stage", width=80)
        sb = ttk.Scrollbar(f, command=self.tv.yview); self.tv.configure(yscrollcommand=sb.set)
        self.tv.pack(side="left", fill="both", expand=True); sb.pack(side="right", fill="y")
        self.tv.bind("<<TreeviewSelect>>", self._info)
        self.tv.bind("<Double-1>", lambda e: self.view())
        act = ttk.Frame(self); act.pack(fill="x", padx=6, pady=3)
        ttk.Label(act, text="Назначить:").pack(side="left")
        self.target = tk.StringVar(value="Base Color (превью)")
        self.targets = ["Base Color (превью)"] + [f"Stage{i} — {STAGE_ROLE[i]}" for i in range(1, 8)]
        ttk.Combobox(act, textvariable=self.target, values=self.targets, state="readonly", width=26).pack(side="left", padx=4)
        ttk.Button(act, text="OK", width=4, command=self.assign).pack(side="left")
        act2 = ttk.Frame(self); act2.pack(fill="x", padx=6, pady=(0, 3))
        ttk.Button(act2, text="Просмотр…", command=self.view, style="Tool.TButton").pack(side="left")
        ttk.Button(act2, text="Убрать из списка", command=self.remove, style="Tool.TButton").pack(side="left", padx=4)
        self.info = ttk.Label(self, text="", style="Hint.TLabel", wraplength=300, justify="left"); self.info.pack(fill="x", padx=6, pady=(0, 6))

    def selected(self):
        return list(self.tv.selection())

    def refresh(self):
        keep = set(self.selected())
        self.tv.delete(*self.tv.get_children())
        q = self.q.get().lower().strip()
        for path in self.app.proj.textures:
            p = Path(path)
            if q and q not in p.name.lower():
                continue
            st = guess_stage(p)
            lab = "Base Color" if st == 0 else (f"Stage{st}" if st else "—")
            arr = self.app.thumbs.peek(p)
            kw = {}
            if arr is not None:
                ph = self._photos.get(path) or np_photo(arr)
                self._photos[path] = ph
                kw["image"] = ph
            self.tv.insert("", "end", iid=path, text=" " + p.name, values=(lab,), **kw)
        for k in keep:
            if self.tv.exists(k):
                self.tv.selection_add(k)

    def _info(self, _=None):
        s = self.selected()
        if not s:
            self.info.configure(text="")
            return
        ti = probe(s[0])
        pr = ti.problems()
        self.info.configure(text=f"{ti.name}\n{ti.width}×{ti.height} · {ti.fmt}{' · α' if ti.has_alpha else ''}" + (("\n⚠ " + "; ".join(pr)) if pr else ""))

    def add_files(self):
        fs = filedialog.askopenfilenames(parent=self, title="Текстуры", filetypes=IMG_TYPES, initialdir=self.app.settings.last_dir or None)
        if fs:
            self.app.add_textures(fs)

    def add_folder(self):
        d = filedialog.askdirectory(parent=self, title="Папка с текстурами", initialdir=self.app.settings.last_dir or None)
        if d:
            self.app.add_textures(scan_folder(d))

    def assign(self):
        s = self.selected()
        if not s:
            return
        i = self.targets.index(self.target.get()) if self.target.get() in self.targets else 0
        self.app.assign_texture(i, Path(s[0]))

    def view(self):
        s = self.selected()
        if s:
            self.app.view_texture(s[0])

    def remove(self):
        gone = set(self.selected())
        self.app.proj.textures = [t for t in self.app.proj.textures if t not in gone]
        self.refresh()

    def auto_assign(self):
        groups = auto_match([Path(t) for t in self.app.proj.textures])
        if not groups:
            messagebox.showinfo("Авто-назначение", "Не найдено файлов с суффиксами _nohq/_as/_smdi/_co.", parent=self)
            return
        names = sorted(groups)
        name = names[0] if len(names) == 1 else ask_text(self, "Авто-назначение", "Набор текстур (общее имя):\n" + ", ".join(names), names[0])
        if name and name.lower() in groups:
            for st, p in groups[name.lower()].items():
                self.app.assign_texture(st, p)


class PresetsTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        r = ttk.Frame(self); r.pack(fill="x", padx=6, pady=6)
        self.q = tk.StringVar(); self.q.trace_add("write", lambda *_: self.refresh())
        ttk.Entry(r, textvariable=self.q).pack(side="left", fill="x", expand=True)
        self.cat = tk.StringVar(value="Все категории")
        cb = ttk.Combobox(r, textvariable=self.cat, values=["Все категории"] + CATEGORIES, state="readonly", width=16)
        cb.pack(side="left", padx=4); cb.bind("<<ComboboxSelected>>", lambda e: self.refresh())
        self.fav = tk.BooleanVar()
        ttk.Checkbutton(r, text="★", variable=self.fav, command=self.refresh).pack(side="left")
        f, self.tv = tree(self, ("name", "cat"), ("Пресет", "Категория"), {"name": 170, "cat": 110}, height=12)
        f.pack(fill="both", expand=True, padx=6)
        self.tv.bind("<<TreeviewSelect>>", self._sel)
        self.tv.bind("<Double-1>", lambda e: self.apply())
        self.desc = ttk.Label(self, text="", style="Hint.TLabel", wraplength=300, justify="left"); self.desc.pack(fill="x", padx=6, pady=4)
        b1 = ttk.Frame(self); b1.pack(fill="x", padx=6)
        for t, c in (("Применить", self.apply), ("★/☆", self.toggle_fav), ("Копия", self.dup), ("Удалить", self.delete)):
            ttk.Button(b1, text=t, command=c, style="Tool.TButton").pack(side="left", padx=(0, 4))
        b2 = ttk.Frame(self); b2.pack(fill="x", padx=6, pady=(4, 8))
        for t, c in (("Сохранить текущий…", app.save_as_preset), ("Импорт", self.imp), ("Экспорт", self.exp)):
            ttk.Button(b2, text=t, command=c, style="Tool.TButton").pack(side="left", padx=(0, 4))
        self.refresh()

    def current(self):
        s = self.tv.selection()
        return s[0] if s else None

    def refresh(self):
        self.tv.delete(*self.tv.get_children())
        cat = "" if self.cat.get() == "Все категории" else self.cat.get()
        for p in self.app.pm.search(self.q.get(), cat, self.fav.get()):
            self.tv.insert("", "end", iid=p.id, values=(("★ " if p.favorite else "") + p.name + ("" if p.builtin else "  (мой)"), p.category))

    def _sel(self, _=None):
        i = self.current()
        self.desc.configure(text=self.app.pm.items[i].description if i else "")

    def apply(self):
        if self.current():
            self.app.apply_preset(self.current())

    def toggle_fav(self):
        i = self.current()
        if i:
            self.app.pm.set_favorite(i, not self.app.pm.items[i].favorite); self.refresh()

    def dup(self):
        if self.current():
            self.app.pm.duplicate(self.current()); self.refresh()

    def delete(self):
        i = self.current()
        if i and messagebox.askyesno("Удалить", "Удалить пресет?", parent=self):
            try:
                self.app.pm.delete(i); self.refresh()
            except PermissionError as e:
                messagebox.showinfo("Пресеты", str(e), parent=self)

    def imp(self):
        f = filedialog.askopenfilename(parent=self, title="Импорт пресетов", filetypes=[("Пресеты", "*.json"), ("Все", "*.*")])
        if f:
            try:
                n = len(self.app.pm.import_from(f)); self.refresh(); messagebox.showinfo("Импорт", f"Импортировано: {n}", parent=self)
            except Exception as e:
                log_exc("panels.imp")
                messagebox.showwarning("Импорт", f"Ошибка: {e}", parent=self)

    def exp(self):
        i = self.current()
        if i:
            f = filedialog.asksaveasfilename(parent=self, defaultextension=".json", initialfile=f"{self.app.pm.items[i].name}.json", filetypes=[("Пресеты", "*.json")])
            if f:
                self.app.pm.export_to([i], f)


class StatesTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        ttk.Label(self, text="Состояния комплекта (☑ — создавать файл; клик по строке — редактировать это состояние):", style="Hint.TLabel", wraplength=300, justify="left").pack(fill="x", padx=6, pady=6)
        f, self.tv = tree(self, ("on", "file"), ("", "Файл"), {"on": 28, "file": 250}, height=7)
        f.pack(fill="x", padx=6)
        self.tv.column("on", width=28, stretch=False)
        self.tv.bind("<ButtonRelease-1>", self._click)
        ttk.Label(self, text="Отличия выбранного от базового:", style="Hint.TLabel").pack(anchor="w", padx=6, pady=(8, 2))
        self.diff = tk.Text(self, height=10, wrap="word"); theme.style_text(self.diff)
        self.diff.pack(fill="both", expand=True, padx=6, pady=(0, 6)); self.diff.configure(state="disabled")

    def refresh(self):
        p = self.app.proj
        sel = self.tv.selection()
        self.tv.delete(*self.tv.get_children())
        for s in ALL_STATES:
            self.tv.insert("", "end", iid=s.value, values=("☑" if s in p.enabled else "☐", kit_filename(p.name, s) + ("" if s.confirmed else "   (Stage3 base не подтверждён)")))
        cur = self.app.target if self.app.target not in (TARGET_SHARED, TARGET_DECOR) else (State.BASE.value if self.app.target == TARGET_SHARED else State.DECORATIVE.value)
        if self.tv.exists(cur):
            self.tv.selection_set(cur)
        self._diff(cur)

    def _click(self, e):
        row = self.tv.identify_row(e.y)
        if not row:
            return
        if self.tv.identify_column(e.x) == "#1":
            self.app.proj.set_enabled(State(row), State(row) not in self.app.proj.enabled)
        else:
            self.app.set_target(TARGET_SHARED if row == State.BASE.value else row)

    def _diff(self, state_value):
        from ..backend import diff_materials
        p = self.app.proj
        d = diff_materials(p.effective(State.BASE), p.effective(State(state_value)))
        self.diff.configure(state="normal"); self.diff.delete("1.0", "end")
        self.diff.insert("1.0", "\n".join(f"{k}: {a} → {b}" for k, a, b in d) or "(нет отличий)")
        self.diff.configure(state="disabled")


class PartsTab(ttk.Frame):
    """Материалы проекта (слоты) и назначение их частям (селекшенам) модели."""

    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self._lock = False
        ttk.Label(self, text="Материалы проекта (у каждого свой комплект RVMAT):", style="Head.TLabel").pack(anchor="w", padx=6, pady=(6, 2))
        f, self.slots = tree(self, ("n", "p"), ("Материал", "Частей"), {"n": 220, "p": 60}, height=4)
        f.pack(fill="x", padx=6)
        self.slots.bind("<<TreeviewSelect>>", self._slot_sel)
        b = ttk.Frame(self); b.pack(fill="x", padx=6, pady=4)
        for t, c in (("+ Новый", self.new), ("Копия", self.dup), ("Имя", self.rename), ("Удалить", self.delete)):
            ttk.Button(b, text=t, command=c, style="Tool.TButton").pack(side="left", padx=(0, 4))
        lr = ttk.Frame(self); lr.pack(fill="x", padx=6)
        ttk.Label(lr, text="Имя селекшена в файлах:").pack(side="left")
        self.label = tk.StringVar()
        e = ttk.Entry(lr, textvariable=self.label, width=18); e.pack(side="left", padx=4)
        e.bind("<Return>", lambda ev: self.set_label()); e.bind("<FocusOut>", lambda ev: self.set_label())
        self.info = ttk.Label(self, text="", style="Hint.TLabel", wraplength=300); self.info.pack(anchor="w", padx=6, pady=(6, 2))
        f2, self.table = tree(self, ("part", "mat"), ("Часть (селекшен)", "Материал (двойной клик — сменить)"), {"part": 130, "mat": 170}, height=8)
        f2.pack(fill="both", expand=True, padx=6)
        self.table.bind("<<TreeviewSelect>>", self._row_sel)
        self.table.bind("<Double-1>", self._menu)
        self.table.bind("<Return>", self._menu)
        ttk.Label(self, text="Клик по части на модели выделяет её. Части без назначения используют первый материал. Части берутся из групп OBJ, объектов FBX или зон тестовых моделей.",
                  style="Hint.TLabel", wraplength=300, justify="left").pack(anchor="w", padx=6, pady=6)

    def refresh(self):
        self._lock = True
        try:
            p = self.app.proj
            mesh = self.app.preview.current_mesh()
            parts = list(mesh.part_names or [])
            used = {s.name: 0 for s in p.slots}
            for pt in parts:
                used[p.slot_for_part(pt).name] += 1
            self.slots.delete(*self.slots.get_children())
            for i, s in enumerate(p.slots):
                self.slots.insert("", "end", iid=str(i), values=(("● " if i == p.active else "   ") + s.name, used[s.name]))
            self.slots.selection_set(str(p.active))
            self.label.set(p.slot.label)
            sel = self.table.selection()
            self.table.delete(*self.table.get_children())
            for i, name in enumerate(parts):
                self.table.insert("", "end", iid=str(i), values=(name, p.slot_for_part(name).name))
            if sel and self.table.exists(sel[0]):
                self.table.selection_set(sel[0])
            self.info.configure(text=f"Модель: {len(parts)} част(ей)" if parts else "У этой модели нет частей (селекшенов): материал применяется ко всей модели.")
        finally:
            self._lock = False

    def select_part(self, idx):
        self._lock = True
        try:
            if idx >= 0 and self.table.exists(str(idx)):
                self.table.selection_set(str(idx))
            else:
                self.table.selection_remove(*self.table.selection())
        finally:
            self._lock = False
        self.app.on_part_selected(idx)

    def _row_sel(self, _=None):
        if self._lock:
            return
        s = self.table.selection()
        self.app.on_part_selected(int(s[0]) if s else -1)

    def _slot_sel(self, _=None):
        if self._lock:
            return
        s = self.slots.selection()
        if s:
            self.app.proj.set_active(int(s[0]))

    def _menu(self, e=None):
        s = self.table.selection()
        if not s:
            return
        part = self.table.item(s[0], "values")[0]
        m = tk.Menu(self, tearoff=0, bg=theme.PANEL, fg=theme.FG, activebackground=theme.ACC)
        for sl in self.app.proj.slots:
            m.add_command(label=sl.name, command=lambda n=sl.name: self.app.proj.assign_part(part, n))
        x, y = (e.x_root, e.y_root) if e is not None and hasattr(e, "x_root") else (self.winfo_pointerx(), self.winfo_pointery())
        m.tk_popup(x, y)

    def set_label(self):
        if not self._lock and self.label.get() != self.app.proj.slot.label:
            self.app.proj._change("Имя селекшена для файлов")
            self.app.proj.slot.label = self.label.get().strip()
            self.app.proj._notify()

    def _try(self, fn):
        try:
            fn()
        except (ValueError, KeyError) as e:
            messagebox.showwarning("Материалы", str(e), parent=self)

    def new(self):
        n = ask_text(self, "Новый материал", "Имя (основа имён файлов, напр. visor):", f"{self.app.proj.title}_part{len(self.app.proj.slots) + 1}")
        if n and n.strip():
            self._try(lambda: self.app.proj.add_slot(n))

    def dup(self):
        n = ask_text(self, "Копия материала", "Имя копии:", self.app.proj.name + "_copy")
        if n and n.strip():
            self._try(lambda: self.app.proj.add_slot(n, copy_from=self.app.proj.active))

    def rename(self):
        n = ask_text(self, "Имя материала", "Имя:", self.app.proj.name)
        if n and n.strip():
            self._try(lambda: self.app.proj.rename(n))

    def delete(self):
        if len(self.app.proj.slots) > 1 and messagebox.askyesno("Удалить", f"Удалить материал «{self.app.proj.name}»?", parent=self):
            self._try(lambda: self.app.proj.remove_slot(self.app.proj.active))


# ================================================================== правая панель
class StageEditor(ttk.Frame):
    def __init__(self, parent, panel, idx):
        super().__init__(parent)
        self.panel, self.idx, self._lock, self._photo = panel, idx, False, None
        p = f"Stage{idx}"
        ttk.Label(self, text=STAGE_ROLE.get(idx, "Дополнительная стадия"), style="Head.TLabel").pack(anchor="w", padx=8, pady=(8, 2))
        self.vis = tk.BooleanVar(value=True)
        ttk.Checkbutton(self, text="Показывать в предпросмотре (в RVMAT остаётся)", variable=self.vis, command=lambda: panel.app.set_stage_visible(idx, self.vis.get())).pack(anchor="w", padx=8)
        g = self.texgroup = ttk.LabelFrame(self, text="Текстура"); g.pack(fill="x", padx=8, pady=6)
        self.thumb = tk.Label(g, width=12, height=6, bg=theme.INPUT, fg=theme.DIM, text="—")
        self.thumb.grid(row=0, column=0, rowspan=3, padx=6, pady=6, sticky="n")
        self.tex = tk.StringVar()
        e = ttk.Entry(g, textvariable=self.tex); e.grid(row=0, column=1, sticky="ew", padx=4, pady=(6, 2))
        e.bind("<Return>", lambda ev: panel.set_value(f"{p}.texture", self.tex.get().strip()))
        e.bind("<FocusOut>", lambda ev: self._tex_focus_out())
        br = ttk.Frame(g); br.grid(row=1, column=1, sticky="w", padx=4)
        ttk.Button(br, text="Файл…", style="Tool.TButton", command=lambda: panel.app.browse_assign(idx)).pack(side="left")
        mb = ttk.Menubutton(br, text="Процедурная ▾"); mb.pack(side="left", padx=4)
        m = tk.Menu(mb, tearoff=0, bg=theme.PANEL, fg=theme.FG, activebackground=theme.ACC)
        m.add_command(label="color (DT: деталь)", command=lambda: panel.set_value(f"{p}.texture", make_color(0.5, 0.5, 0.5, 1, "DT")))
        m.add_command(label="color (MC: условие)", command=lambda: panel.set_value(f"{p}.texture", make_color(0, 0, 0, 0, "MC")))
        m.add_command(label="fresnel(n,k)", command=lambda: panel.set_value(f"{p}.texture", make_fresnel(0.88, 1.89)))
        mb["menu"] = m
        ttk.Button(br, text="Просмотр", style="Tool.TButton", command=lambda: panel.app.view_stage_texture(idx)).pack(side="left")
        self.info = ttk.Label(g, text="", style="Hint.TLabel", wraplength=300, justify="left"); self.info.grid(row=2, column=1, sticky="w", padx=4, pady=(2, 6))
        g.columnconfigure(1, weight=1)
        # процедурная
        self.pg = ttk.LabelFrame(self, text="Процедурная текстура")
        self.pc = [tk.StringVar() for _ in range(4)]; self.ptag = tk.StringVar(); self.fn = tk.StringVar(); self.fk = tk.StringVar()
        self.pcw = ttk.Frame(self.pg); self.pcw.pack(fill="x", padx=6, pady=4)
        for v, n in zip(self.pc, "RGBA"):
            ttk.Label(self.pcw, text=n).pack(side="left"); sp = ttk.Spinbox(self.pcw, from_=0, to=1, increment=0.05, textvariable=v, width=6, command=self._color); sp.pack(side="left", padx=(1, 6))
            sp.bind("<Return>", lambda ev: self._color()); sp.bind("<FocusOut>", lambda ev: self._color())
        ttk.Label(self.pcw, text="Тег").pack(side="left")
        te = ttk.Entry(self.pcw, textvariable=self.ptag, width=6); te.pack(side="left", padx=2); te.bind("<Return>", lambda ev: self._color())
        self.pfw = ttk.Frame(self.pg); self.pfw.pack(fill="x", padx=6, pady=4)
        for v, n in ((self.fn, "n"), (self.fk, "k")):
            ttk.Label(self.pfw, text=n).pack(side="left"); sp = ttk.Spinbox(self.pfw, from_=0, to=20, increment=0.01, textvariable=v, width=8, command=self._fres); sp.pack(side="left", padx=(1, 8))
            sp.bind("<Return>", lambda ev: self._fres()); sp.bind("<FocusOut>", lambda ev: self._fres())
        # UV
        ug = ttk.LabelFrame(self, text="UV Transform"); ug.pack(fill="x", padx=8, pady=6)
        r = ttk.Frame(ug); r.pack(fill="x", padx=6, pady=(4, 2))
        ttk.Label(r, text="uvSource").pack(side="left")
        self.src = tk.StringVar(); cb = ttk.Combobox(r, textvariable=self.src, values=UV_SOURCES, width=10); cb.pack(side="left", padx=6)
        cb.bind("<<ComboboxSelected>>", lambda e: panel.set_value(f"{p}.uvSource", self.src.get())); cb.bind("<Return>", lambda e: panel.set_value(f"{p}.uvSource", self.src.get()))
        self.uv = {}
        grid = ttk.Frame(ug); grid.pack(fill="x", padx=6, pady=2)
        for ri, k in enumerate(("aside", "up", "dir", "pos")):
            ttk.Label(grid, text=k, width=6).grid(row=ri, column=0, sticky="w")
            row = []
            for c in range(3):
                v = tk.StringVar(); sp = ttk.Spinbox(grid, from_=-1000, to=1000, increment=0.1, textvariable=v, width=9, command=lambda k=k: self._uv(k))
                sp.grid(row=ri, column=1 + c, padx=2, pady=1); sp.bind("<Return>", lambda e, k=k: self._uv(k)); sp.bind("<FocusOut>", lambda e, k=k: self._uv(k)); row.append(v)
            self.uv[k] = row
        hr = ttk.Frame(ug); hr.pack(fill="x", padx=6, pady=(2, 6))
        self.h = {}
        for k, n in (("tile_u", "Tile U"), ("tile_v", "Tile V"), ("offset_u", "Off U"), ("offset_v", "Off V"), ("rotation", "Rot°")):
            ttk.Label(hr, text=n).pack(side="left"); v = tk.StringVar(); sp = ttk.Spinbox(hr, from_=-360, to=360, increment=0.1, textvariable=v, width=6, command=self._helper); sp.pack(side="left", padx=(1, 6))
            sp.bind("<Return>", lambda e: self._helper()); sp.bind("<FocusOut>", lambda e: self._helper()); self.h[k] = v
        fr = ttk.Frame(self); fr.pack(fill="x", padx=8)
        ttk.Label(fr, text="Filter").pack(side="left")
        self.filt = tk.StringVar(); fc = ttk.Combobox(fr, textvariable=self.filt, values=FILTERS, width=14); fc.pack(side="left", padx=6)
        fc.bind("<<ComboboxSelected>>", lambda e: panel.set_value(f"{p}.Filter", self.filt.get() or None)); fc.bind("<Return>", lambda e: panel.set_value(f"{p}.Filter", self.filt.get() or None))
        self.extra = ttk.Label(self, text="", style="Hint.TLabel", wraplength=480, justify="left"); self.extra.pack(anchor="w", padx=8, pady=6)

    def _tex_focus_out(self):
        st = self.panel.stage_json(self.idx)
        if st is not None and self.tex.get().strip() != st.texture:
            self.panel.set_value(f"Stage{self.idx}.texture", self.tex.get().strip())

    def _num(self, v, d=0.0):
        try:
            return float(v.get().replace(",", "."))
        except ValueError:
            return d

    def _color(self):
        if not self._lock:
            self.panel.set_value(f"Stage{self.idx}.texture", make_color(*[self._num(v) for v in self.pc], (self.ptag.get().strip() or "DT")))

    def _fres(self):
        if not self._lock:
            self.panel.set_value(f"Stage{self.idx}.texture", make_fresnel(self._num(self.fn), self._num(self.fk)))

    def _uv(self, k):
        if not self._lock:
            self.panel.set_value(f"Stage{self.idx}.uvTransform.{k}", [self._num(v) for v in self.uv[k]])

    def _helper(self):
        if self._lock:
            return
        h = {k: self._num(v) for k, v in self.h.items()}
        c, s = math.cos(math.radians(h["rotation"])), math.sin(math.radians(h["rotation"]))
        r = lambda x: round(x, 6) + 0.0
        pre = f"Stage{self.idx}.uvTransform."
        pos = self.panel.current_uv(self.idx, "pos") or [0, 0, 0]
        self.panel.set_value(pre + "aside", [r(h["tile_u"] * c), r(h["tile_u"] * s), 0], "UV tiling")
        self.panel.set_value(pre + "up", [r(-h["tile_v"] * s), r(h["tile_v"] * c), 0], "UV tiling")
        self.panel.set_value(pre + "pos", [h["offset_u"], h["offset_v"], pos[2]], "UV offset")

    def refresh(self, mat, find, thumbs):
        self._lock = True
        try:
            st = mat.stage(self.idx)
            if st is None:
                self.tex.set(""); self.info.configure(text="Стадия отсутствует в материале. Задайте текстуру, чтобы создать её.")
                self.thumb.configure(image="", text="—", width=12, height=6); self.pg.pack_forget()
                return
            tex = st.texture or ""
            self.tex.set(tex)
            proc = is_procedural(tex)
            self.thumb.configure(image="", text="", width=12, height=6)
            if proc:
                self.thumb.configure(text="процедур.")
                try:
                    pr = parse_procedural(tex)
                    if pr.func == "color":
                        for v, x in zip(self.pc, pr.rgba or (0, 0, 0, 0)):
                            v.set(fmt(x))
                        self.ptag.set(pr.tag or "")
                        self.pcw.pack(fill="x", padx=6, pady=4); self.pfw.pack_forget()
                    else:
                        if pr.fresnel:
                            self.fn.set(fmt(pr.fresnel[0])); self.fk.set(fmt(pr.fresnel[1]))
                        self.pfw.pack(fill="x", padx=6, pady=4); self.pcw.pack_forget()
                    self.pg.pack(fill="x", padx=8, pady=6, after=self.texgroup)
                    self.info.configure(text=f"Функция: {pr.func}")
                except ValueError as e:
                    self.info.configure(text=f"Ошибка: {e}")
            else:
                self.pg.pack_forget()
                f = find(tex) if tex else None
                if f:
                    arr = thumbs.peek(Path(f))
                    if arr is not None:
                        self._photo = np_photo(arr)
                        self.thumb.configure(image=self._photo, text="", width=96, height=96)
                    self.info.configure(text=f"Файл: {f}")
                else:
                    self.thumb.configure(text="нет файла")
                    self.info.configure(text="Файл не найден в корне проекта/сопоставлениях (в игре может существовать)")
            self.src.set(st.uv_source or ""); self.filt.set(st.filter or "")
            for k in ("aside", "up", "dir", "pos"):
                for v, x in zip(self.uv[k], st.uv(k) or [0, 0, 0]):
                    v.set(fmt(x, 5))
            for k, x in st.uv_params().items():
                self.h[k].set(fmt(x, 4))
            extras = [n.name for n in st.node.props() if n.name not in {"texture", "uvSource", "Filter"}] + [c.name for c in st.node.classes() if c.name != "uvTransform"]
            self.extra.configure(text=("Дополнительные (сохраняются): " + ", ".join(extras)) if extras else "")
        finally:
            self._lock = False


class PropsPanel(ttk.Frame):
    TARGET_NAMES = {TARGET_SHARED: "Общий материал (все состояния)", TARGET_DECOR: "Декоративная ветка (все decorative_*)"}

    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self._lock = False
        top = ttk.Frame(self); top.pack(fill="x", padx=6, pady=6)
        ttk.Label(top, text="Редактируется:").pack(side="left")
        self.ids = [TARGET_SHARED, TARGET_DECOR] + [s.value for s in State]
        self.names = [self.TARGET_NAMES.get(i, f"Только: {i}") for i in self.ids]
        self.tsel = tk.StringVar(value=self.names[0])
        cb = ttk.Combobox(top, textvariable=self.tsel, values=self.names, state="readonly"); cb.pack(side="left", fill="x", expand=True, padx=6)
        cb.bind("<<ComboboxSelected>>", lambda e: app.set_target(self.ids[self.names.index(self.tsel.get())]))
        ttk.Label(self, text="Оранжевое название = переопределение для выбранного варианта. ↺ убирает его.", style="Hint.TLabel", wraplength=500).pack(anchor="w", padx=8)
        self.nb = ttk.Notebook(self); self.nb.pack(fill="both", expand=True, pady=(4, 0))
        self.mat_tab = ScrollFrame(self.nb); self.nb.add(self.mat_tab, text="Материал")
        self._build_material(self.mat_tab.inner)
        self.stages = {}
        for i in range(1, 8):
            sf = ScrollFrame(self.nb); self.nb.add(sf, text=f"S{i}")
            ed = StageEditor(sf.inner, self, i); ed.pack(fill="both", expand=True); self.stages[i] = ed

    # ---- доступ к данным
    def view_material(self):
        p = self.app.proj
        return p.spec.shared if self.app.target == TARGET_SHARED else p.effective(self.app.view_state())

    def stage_json(self, idx):
        return self.view_material().stage(idx)

    def current_uv(self, idx, key):
        st = self.view_material().stage(idx)
        return st.uv(key) if st else None

    def set_value(self, path, value, label=""):
        if not self._lock:
            self.app.proj.set_value(self.app.target, path, value, label or f"{self.app.target}: {path}")

    def reset(self, path):
        self.app.proj.reset_to_baseline(self.app.target, path)

    # ---- вкладка «Материал»
    def _build_material(self, root):
        q = ttk.LabelFrame(root, text="Быстрая настройка"); q.pack(fill="x", padx=8, pady=8)
        self.quick, self.notes = {}, {}
        for key, (title, left, right, fields, why) in S.CONTROLS.items():
            row = SliderRow(q, title, 0, 100, 1, 0, " %", on_change=lambda v, k=key: self._quick(k, v), on_reset=lambda k=key: self._quick_reset(k), label_width=15)
            row.pack(fill="x", padx=6, pady=(4, 0))
            n = ttk.Label(q, text="", style="Hint.TLabel", wraplength=470, justify="left"); n.pack(anchor="w", padx=8)
            self.quick[key], self.notes[key] = row, n
        row = SliderRow(q, "Сила нормали", 0, 300, 5, 0, " %", on_change=lambda v: None if self._lock else self.app.proj.set_normal_strength(v / 100.0),
                        on_reset=lambda: self.app.proj.set_normal_strength(1.0), label_width=15)
        row.pack(fill="x", padx=6, pady=(4, 0))
        n = ttk.Label(q, text="0 — плоско · 100 — как в текстуре · 300 — втрое сильнее. Только предпросмотр, в RVMAT не пишется.", style="Hint.TLabel", wraplength=470, justify="left")
        n.pack(anchor="w", padx=8, pady=(0, 6))
        self.quick["normal"], self.notes["normal"] = row, n
        bg = ttk.LabelFrame(root, text="Base Color (_co) — только для предпросмотра"); bg.pack(fill="x", padx=8, pady=4)
        self.base = tk.StringVar()
        e = ttk.Entry(bg, textvariable=self.base, state="readonly"); e.pack(side="left", fill="x", expand=True, padx=6, pady=6)
        ttk.Button(bg, text="Файл…", style="Tool.TButton", command=self.app.browse_base).pack(side="left")
        ttk.Button(bg, text="✕", width=3, style="Tool.TButton", command=lambda: self.app.proj.set_base_color("")).pack(side="left", padx=6)
        ttk.Label(root, text="В DayZ базовый цвет задаётся текстурой грани модели, а не RVMAT.", style="Hint.TLabel").pack(anchor="w", padx=10)
        self.adv_btn = ttk.Button(root, text="▸ Подробно: цвета RGBA, Specular Power, шейдеры (поля RVMAT как есть)", command=self._toggle_adv)
        self.adv_btn.pack(fill="x", padx=8, pady=(10, 2))
        self.adv = ttk.Frame(root)
        self.colors = {}
        for name in COLOR_LABELS:
            r = ColorRow(self.adv, COLOR_LABELS[name], on_change=lambda v, n=name: self.set_value(n, v), on_reset=lambda n=name: self.reset(n))
            r.pack(fill="x", padx=6, pady=2); self.colors[name] = r
        self.power = SliderRow(self.adv, "Specular Power", 0, 200, 0.5, 2, "", on_change=lambda v: self.set_value("specularPower", v), on_reset=lambda: self.reset("specularPower"))
        self.power.pack(fill="x", padx=6, pady=2)
        sh = ttk.Frame(self.adv); sh.pack(fill="x", padx=6, pady=4)
        self.ps, self.vs = tk.StringVar(), tk.StringVar()
        for r_, (v, n, lab) in enumerate(((self.ps, "PixelShaderID", "Pixel Shader ID"), (self.vs, "VertexShaderID", "Vertex Shader ID"))):
            ttk.Label(sh, text=lab, width=16).grid(row=r_, column=0, sticky="w")
            cb = ttk.Combobox(sh, textvariable=v, values=SHADERS, width=18); cb.grid(row=r_, column=1, sticky="w", pady=2)
            cb.bind("<<ComboboxSelected>>", lambda e, v=v, n=n: self.set_value(n, v.get())); cb.bind("<Return>", lambda e, v=v, n=n: self.set_value(n, v.get()))
        self.unknown = ttk.Label(root, text="", style="Hint.TLabel", wraplength=480, justify="left"); self.unknown.pack(anchor="w", padx=8, pady=6)
        self._adv_open = False

    def _toggle_adv(self):
        self._adv_open = not self._adv_open
        if self._adv_open:
            self.adv.pack(fill="x", padx=8, after=self.adv_btn)
        else:
            self.adv.pack_forget()
        self.adv_btn.configure(text=("▾" if self._adv_open else "▸") + " Подробно: цвета RGBA, Specular Power, шейдеры (поля RVMAT как есть)")

    def _quick(self, key, value):
        if self._lock:
            return
        m = self.view_material()
        fn = {"rough": S.set_roughness, "shine": S.set_shine, "metal": S.set_metallic, "glow": S.set_glow}[key]
        for path, val in fn(m, value).items():                    # одна правка = один шаг отмены
            self.set_value(path, val, S.CONTROLS[key][0])

    def _quick_reset(self, key):
        for path in S.CONTROLS[key][3]:
            self.reset(path)

    def refresh(self):
        self._lock = True
        try:
            p, t = self.app.proj, self.app.target
            self.tsel.set(self.names[self.ids.index(t)] if t in self.ids else self.names[0])
            m = self.view_material()
            ov = lambda path: p.is_overridden(t, path)
            vals = {"rough": S.roughness(m), "shine": S.shine(m), "metal": S.metallic(m), "glow": S.glow(m)}
            for key, row in self.quick.items():
                if key == "normal":
                    row.set(p.slot.normal_strength * 100.0); row.mark(abs(p.slot.normal_strength - 1.0) > 1e-6)
                    continue
                row.set(round(vals[key])); row.mark(any(ov(x) for x in S.CONTROLS[key][3]))
            sp, df, em = m.get("specular") or [0, 0, 0], m.get("diffuse") or [0, 0, 0], m.get("emmisive") or [0, 0, 0]
            hints = {"rough": f"→ specularPower = {m.get('specularPower') or 0:g}", "shine": f"→ specular = {S.mean_rgb(sp):.2f}",
                     "metal": f"→ diffuse = {S.mean_rgb(df):.2f}, specular = {S.mean_rgb(sp):.2f}", "glow": f"→ emmisive = {S.mean_rgb(em):.2f}"}
            for key, txt in hints.items():
                self.notes[key].configure(text=f"0 — {S.CONTROLS[key][1]}   ·   100 — {S.CONTROLS[key][2]}\n{txt}")
            self.base.set(p.base_color)
            for n, r in self.colors.items():
                v = m.get(n)
                if v:
                    r.set(v)
                r.mark(ov(n))
            self.power.set(m.get("specularPower") or 0); self.power.mark(ov("specularPower"))
            self.ps.set(m.get("PixelShaderID") or ""); self.vs.set(m.get("VertexShaderID") or "")
            known = set(COLOR_LABELS) | {"specularPower", "PixelShaderID", "VertexShaderID"}
            unk = [x.name for x in m.root.props() if x.name not in known]
            self.unknown.configure(text=("Прочие параметры (сохраняются как есть): " + ", ".join(unk)) if unk else "")
            for ed in self.stages.values():
                ed.refresh(m, self.app.find_texture, self.app.thumbs)
        finally:
            self._lock = False

    def goto(self, location: str):
        loc = location.split(":", 1)[-1]
        if loc.lower().startswith("stage") and loc[5:6].isdigit():
            self.nb.select(int(loc[5]))
        else:
            self.nb.select(0)


# ================================================================== нижняя панель
class BottomPanel(ttk.Notebook):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        f, self.issues = tree(self, ("sev", "code", "msg", "loc"), ("Тип", "Код", "Сообщение", "Расположение"), {"sev": 110, "code": 60, "msg": 520, "loc": 330})
        self.add(f, text="Ошибки и предупреждения")
        self.issues.tag_configure("ERROR", foreground=theme.ERR); self.issues.tag_configure("WARNING", foreground=theme.WARN); self.issues.tag_configure("INFO", foreground=theme.INFO)
        self.issues.bind("<Double-1>", lambda e: self.goto())
        lf = ttk.Frame(self); self.log = tk.Text(lf, state="disabled", wrap="word"); theme.style_text(self.log)
        sb = ttk.Scrollbar(lf, command=self.log.yview); self.log.configure(yscrollcommand=sb.set)
        self.log.pack(side="left", fill="both", expand=True); sb.pack(side="right", fill="y")
        self.add(lf, text="Журнал")
        hf, self.hist = tree(self, ("h",), ("Действие",), {"h": 700})
        self.add(hf, text="История изменений")

    def show_issues(self, issues):
        self.issues.delete(*self.issues.get_children())
        for i in issues:
            self.issues.insert("", "end", values=(SEV[i.severity.name], i.code, i.message, i.location), tags=(i.severity.name,))
        n = [sum(1 for i in issues if i.severity == s) for s in (Severity.ERROR, Severity.WARNING, Severity.INFO)]
        self.tab(0, text=f"Ошибки и предупреждения ({n[0]}/{n[1]}/{n[2]})")

    def goto(self):
        s = self.issues.selection()
        if s:
            self.app.goto_issue(self.issues.item(s[0], "values")[3])

    def set_log(self, lines):
        self.log.configure(state="normal"); self.log.delete("1.0", "end"); self.log.insert("1.0", "\n".join(lines)); self.log.configure(state="disabled"); self.log.see("end")

    def set_history(self, items):
        self.hist.delete(*self.hist.get_children())
        for it in reversed(items[-200:]):
            self.hist.insert("", "end", values=(it,))
