"""Главное окно DayZ RVMAT Studio (tkinter, без Qt)."""
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import numpy as np

from .. import __version__
from ..backend import ALL_STATES, Material, State, kit_filename
from ..core import autoload, dayztools, fbx, meshes
from ..core.presets import CATEGORIES, PresetManager
from ..core.project import TARGET_DECOR, TARGET_SHARED, Project
from ..core.render import DIFFERENCES, LIGHTING, make_resolver
from ..core.settings import Settings
from ..core.texnames import to_game_path, unique_game_path
from ..core.textures import ThumbCache, convert_to_paa
from . import theme
from .dialogs import CandidateDialog, ExportDialog, SettingsDialog, TextureViewer
from .panels import BottomPanel, IMG_TYPES, LeftPanel, PropsPanel
from .preview import PreviewView
from .widgets import ask_text

VIEWS = [("Итог", "final"), ("Base Color (_co)", "albedo"), ("AS: затенение ambient", "ao"), ("Нормали", "normal"), ("Specular (SMDI-G)", "specular"),
         ("SMDI-R (должен быть 1)", "metal"), ("Gloss (SMDI-B)", "gloss"), ("Свечение (emmisive)", "emissive"), ("Detail", "detail"), ("UV-шашечки", "checker")]


class StudioApp:
    def __init__(self, settings=None, preset_manager=None, root=None):
        self.root = root or tk.Tk()
        self.settings = settings or Settings.load()
        self.pm = preset_manager or PresetManager()
        self.proj = Project()
        if self.settings.project_root:
            self.proj.project_root = self.settings.project_root
        self.thumbs = ThumbCache(48)
        self.resolver = make_resolver(None)
        self.target = TARGET_SHARED
        self._refresh_job = self._validate_job = self._auto_job = None
        self._closing = False
        r = self.root
        r.title("DayZ RVMAT Studio")
        r.geometry(self.settings.geometry or "1500x900")
        theme.apply(r)
        self._build_menu()
        self._build_toolbar()
        self.status = tk.StringVar()
        outer = ttk.PanedWindow(r, orient="vertical"); outer.pack(fill="both", expand=True)
        main = ttk.PanedWindow(outer, orient="horizontal"); outer.add(main, weight=5)
        center = ttk.Frame(main)
        self._build_viewport_bar(center)
        self.preview = PreviewView(center, on_pick=self._picked, on_status=self.toast, renderer=self.settings.renderer)
        self.preview.pack(fill="both", expand=True)
        self.left = LeftPanel(main, self)
        self.props = PropsPanel(main, self)
        main.add(self.left, weight=0); main.add(center, weight=1); main.add(self.props, weight=0)
        self.left.configure(width=340); self.props.configure(width=560)
        self.bottom = BottomPanel(outer, self)
        outer.add(self.bottom, weight=1)
        ttk.Label(r, textvariable=self.status, style="Hint.TLabel", anchor="w").pack(fill="x", side="bottom", padx=8)
        self.outer, self.main = outer, main
        self.proj.listeners.append(self.on_project_changed)
        self._bind_keys()
        r.after(60, self._init_layout)
        r.protocol("WM_DELETE_WINDOW", self.close)
        self.toasts: list = []
        self.log(f"DayZ RVMAT Studio {__version__} запущен · рендер: {self.preview.backend_info}")
        self._thumb_ver = 0
        self.root.after(400, self._poll_thumbs)
        self.flush()
        self._schedule_autosave()

    # ------------------------------------------------------------ построение
    def _build_menu(self):
        r = self.root
        mb = tk.Menu(r, bg=theme.PANEL, fg=theme.FG, activebackground=theme.ACC, activeforeground="#fff", bd=0)
        m = lambda: tk.Menu(mb, tearoff=0, bg=theme.PANEL, fg=theme.FG, activebackground=theme.ACC, activeforeground="#fff")
        f = m(); mb.add_cascade(label="Файл", menu=f)
        f.add_command(label="Новый проект", accelerator="Ctrl+N", command=self.new_project)
        f.add_command(label="Открыть проект…", accelerator="Ctrl+O", command=self.open_project)
        self.recent_menu = m(); f.add_cascade(label="Недавние", menu=self.recent_menu)
        f.add_command(label="Сохранить", accelerator="Ctrl+S", command=self.save_project)
        f.add_command(label="Сохранить как…", accelerator="Ctrl+Shift+S", command=lambda: self.save_project(True))
        f.add_separator()
        f.add_command(label="Открыть папку проекта (FBX + data)…", accelerator="Ctrl+Shift+O", command=self.load_project_folder)
        f.add_command(label="Импорт RVMAT (один или комплект)…", accelerator="Ctrl+I", command=self.import_rvmat)
        f.add_command(label="Экспорт…", accelerator="Ctrl+E", command=self.export)
        f.add_separator(); f.add_command(label="Выход", accelerator="Ctrl+Q", command=self.close)
        e = m(); mb.add_cascade(label="Правка", menu=e)
        e.add_command(label="Отменить", accelerator="Ctrl+Z", command=self.undo)
        e.add_command(label="Повторить", accelerator="Ctrl+Y", command=self.redo)
        e.add_separator()
        e.add_command(label="Копировать стадию в другое состояние…", command=self.copy_stage)
        e.add_command(label="Переименовать материал…", accelerator="F2", command=lambda: self.left.parts.rename())
        e.add_command(label="Пакетное редактирование RVMAT…", command=self.batch_dialog)
        t = m(); mb.add_cascade(label="Инструменты", menu=t)
        t.add_command(label="Проверить (диагностика)", accelerator="F5", command=self.run_validation)
        t.add_command(label="Корень проекта / P: Drive…", command=self.set_project_root)
        t.add_command(label="Конвертировать текстуры в PAA (DayZ Tools)…", command=self.convert_textures)
        t.add_command(label="Проверить в Workbench…", command=self.open_workbench)
        t.add_command(label="Настройки…", accelerator="Ctrl+,", command=self.open_settings)
        h = m(); mb.add_cascade(label="Справка", menu=h)
        h.add_command(label="Отличия предпросмотра от игры", command=lambda: messagebox.showinfo("Отличия от игры", "\n\n".join("• " + d for d in DIFFERENCES), parent=self.root))
        h.add_command(label="О программе", command=lambda: messagebox.showinfo("DayZ RVMAT Studio", f"DayZ RVMAT Studio {__version__}\nГенератор и редактор RVMAT для DayZ.\n\nРендер: {self.preview.backend_info}", parent=self.root))
        r.configure(menu=mb)
        self.menubar = mb
        self._fill_recent()

    def _build_toolbar(self):
        bar = ttk.Frame(self.root, style="Panel.TFrame"); bar.pack(fill="x")
        def b(text, cmd, style="Tool.TButton", **kw):
            w = ttk.Button(bar, text=text, command=cmd, style=style, **kw); w.pack(side="left", padx=2, pady=4); return w
        b("Новый", self.new_project); b("Открыть", self.open_project); b("Сохранить", self.save_project); b("Импорт RVMAT", self.import_rvmat)
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=6, pady=4)
        self.btn_undo = b("↶", self.undo, width=3); self.btn_redo = b("↷", self.redo, width=3)
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=6, pady=4)
        b("Диагностика", self.run_validation); b("Настройки", self.open_settings)
        self.export_btn = ttk.Button(bar, text="Экспорт…", style="Accent.TButton", command=self.export); self.export_btn.pack(side="right", padx=8, pady=4)
        self.title_lbl = ttk.Label(bar, text="", style="Hint.TLabel", background=theme.PANEL); self.title_lbl.pack(side="right", padx=10)

    def _build_viewport_bar(self, parent):
        bar = ttk.Frame(parent, style="Panel.TFrame"); bar.pack(fill="x")
        rows = [ttk.Frame(bar, style="Panel.TFrame") for _ in range(4)]
        for i, r in enumerate(rows):
            r.pack(fill="x", padx=4, pady=(4 if i == 0 else 0, 4 if i == 3 else 0))
        r1, r2, r3, r4 = rows
        self.shape = tk.StringVar(value="Сфера"); self.light = tk.StringVar(value="Студия"); self.view = tk.StringVar(value=VIEWS[0][0])
        self.tiling = tk.StringVar(value="1"); self.lrot = tk.StringVar(value="0"); self.envg = tk.StringVar(value="1")
        self.wire, self.grid, self.uv, self.cmp_on = (tk.BooleanVar() for _ in range(4))
        self.cmp_state = tk.StringVar(value=State.DAMAGE.value)
        from ..core.meshes import SHAPES
        def lab(p, t):
            ttk.Label(p, text=t, background=theme.PANEL, style="Hint.TLabel").pack(side="left", padx=(8, 2))
        lab(r1, "Модель"); self.shape_cb = ttk.Combobox(r1, textvariable=self.shape, values=list(SHAPES), state="readonly", width=14); self.shape_cb.pack(side="left")
        lab(r1, "Свет"); lc = ttk.Combobox(r1, textvariable=self.light, values=list(LIGHTING), state="readonly", width=11); lc.pack(side="left")
        lab(r1, "Вид"); vc = ttk.Combobox(r1, textvariable=self.view, values=[v[0] for v in VIEWS], state="readonly", width=21); vc.pack(side="left")
        for t, v, lo, hi, inc in (("Повтор ×", self.tiling, 0.1, 32, 0.5), ("Свет ∠", self.lrot, -180, 180, 15), ("Отражения ×", self.envg, 0, 8, 0.25)):
            lab(r2, t); sp = ttk.Spinbox(r2, from_=lo, to=hi, increment=inc, textvariable=v, width=6, command=self._opts_changed); sp.pack(side="left")
            sp.bind("<Return>", lambda e: self._opts_changed()); sp.bind("<FocusOut>", lambda e: self._opts_changed())
        for t, v in (("Сетка", self.wire), ("Решётка", self.grid), ("UV-развёртка", self.uv)):
            ttk.Checkbutton(r3, text=t, variable=v, command=self._opts_changed).pack(side="left", padx=(8, 0))
        ttk.Checkbutton(r3, text="Сравнить с:", variable=self.cmp_on, command=self._opts_changed).pack(side="left", padx=(16, 4))
        cc = ttk.Combobox(r3, textvariable=self.cmp_state, values=[s.value for s in State], state="readonly", width=20); cc.pack(side="left", padx=4)
        cc.bind("<<ComboboxSelected>>", lambda e: self.refresh_preview())
        for t, c in (("Модель…", self.import_model), ("HDRI…", self.load_hdri), ("⟲ Камера", lambda: self.preview.reset_camera()), ("Снимок…", self.snapshot)):
            ttk.Button(r4, text=t, command=c, style="Tool.TButton").pack(side="left", padx=2)
        for cb in (self.shape_cb, lc, vc):
            cb.bind("<<ComboboxSelected>>", lambda e: self._opts_changed())

    def _bind_keys(self):
        r = self.root
        for k, f in (("<Control-n>", self.new_project), ("<Control-o>", self.open_project), ("<Control-s>", self.save_project), ("<Control-i>", self.import_rvmat),
                     ("<Control-e>", self.export), ("<Control-z>", self.undo), ("<Control-y>", self.redo), ("<F5>", self.run_validation),
                     ("<Control-q>", self.close), ("<Control-comma>", self.open_settings), ("<F2>", lambda: self.left.parts.rename())):
            r.bind(k, lambda e, f=f: (f(), "break")[1])
        r.bind("<Control-S>", lambda e: (self.save_project(True), "break")[1])
        r.bind("<Control-O>", lambda e: (self.load_project_folder(), "break")[1])

    def _init_layout(self):
        """Начальные пропорции: слева 330, справа 540 px, снизу ≈ 25 % высоты."""
        try:
            self.root.update_idletasks()
            W, H = self.root.winfo_width(), self.outer.winfo_height()
            self.main.sashpos(0, 330)
            self.main.sashpos(1, max(W - 560, 700))
            self.outer.sashpos(0, int(H * 0.74))
        except tk.TclError:
            pass

    def _poll_thumbs(self):
        """Миниатюры считаются в фоне: когда появились новые — обновить списки."""
        if self._closing:
            return
        if self.thumbs.version != self._thumb_ver:
            self._thumb_ver = self.thumbs.version
            self.left.tex.refresh()
            self.props.refresh()
        self.root.after(400, self._poll_thumbs)

    # ------------------------------------------------------------ вспомогательное
    def log(self, msg: str):
        import time
        self._log = getattr(self, "_log", [])
        self._log.append(f"{time.strftime('%H:%M:%S')}  {msg}")
        del self._log[:-300]
        self.status.set(msg)
        if hasattr(self, "bottom"):
            self.bottom.set_log(self._log)

    def toast(self, msg):
        self.status.set(msg)

    def after(self, ms, fn):
        return self.root.after(ms, fn)

    def find_texture(self, tex):
        return self.resolver.find(tex) if tex else None

    def rebuild_resolver(self):
        root = Path(self.proj.project_root) if self.proj.project_root else None
        self.resolver = make_resolver(root, mapping=self.proj.tex_map)

    def view_state(self) -> State:
        return State.DECORATIVE if self.target == TARGET_DECOR else (State.BASE if self.target == TARGET_SHARED else State(self.target))

    def set_target(self, t):
        self.target = t
        self.flush()

    # ------------------------------------------------------------ обновление интерфейса
    def on_project_changed(self):
        """Коалесцирует серию изменений (перетаскивание ползунка) в одно обновление."""
        if self._refresh_job is None and not self._closing:
            self._refresh_job = self.root.after(15, self.flush)

    def flush(self):
        if self._refresh_job:
            try:
                self.root.after_cancel(self._refresh_job)
            except tk.TclError:
                pass
        self._refresh_job = None
        if self._closing:
            return
        p = self.proj
        self.props.refresh()
        self.left.refresh()
        self.bottom.set_history(p.history)
        self.refresh_preview()
        multi = len(p.slots) > 1
        self.root.title(f"{'*' if p.dirty else ''}{p.title}{' / ' + p.name if multi else ''} — DayZ RVMAT Studio")
        self.title_lbl.configure(text=f"{p.title} · материалов: {len(p.slots)} · редактируется: {p.name}")
        self.btn_undo.state(["!disabled"] if p.can_undo else ["disabled"]); self.btn_redo.state(["!disabled"] if p.can_redo else ["disabled"])
        if self._validate_job:
            self.root.after_cancel(self._validate_job)
        self._validate_job = self.root.after(300, self.run_validation)

    def _opts_changed(self):
        o = self.preview.opts
        def num(v, d):
            try:
                return float(v.get().replace(",", "."))
            except ValueError:
                return d
        self.preview.shape = self.shape.get()
        o.lighting = self.light.get()
        o.view = next(v for t, v in VIEWS if t == self.view.get())
        o.tiling, o.light_rot, o.env_gain = max(num(self.tiling, 1), 0.1), num(self.lrot, 0), num(self.envg, 1)
        o.wire, o.grid = self.wire.get(), self.grid.get()
        self.preview.show_uv = self.uv.get()
        o.highlight = -1 if self.preview.shape != getattr(self, "_last_shape", None) else o.highlight
        self._last_shape = self.preview.shape
        self.left.parts.refresh()
        self.refresh_preview()

    def refresh_preview(self):
        p = self.proj
        st = self.view_state()
        a = p.effective(st)
        cmp_state = State(self.cmp_state.get()) if self.cmp_on.get() else None
        b = p.effective(cmp_state) if cmp_state else None
        o = self.preview.opts
        gl = self.preview.backend == "gl"
        base = (p.base_color or None) if gl else (self.resolver(p.base_color) if p.base_color else None)   # GPU берёт файл в полном размере
        o.base_color = base
        o.normal_strength = p.slot.normal_strength
        s4 = a.stage(4)
        uvb = self.resolver(p.base_color) if p.base_color else None
        if uvb is None and s4 and s4.texture:
            uvb = self.resolver(s4.texture)
        self.preview.uv_bg = uvb
        self.preview.set_materials(a, b, self.resolver, self._parts_mats(st), self._parts_mats(cmp_state) if cmp_state else None)

    def _parts_mats(self, state):
        mesh = self.preview.current_mesh()
        if mesh.face_part is None:
            return None
        out = {}
        for i, name in enumerate(mesh.part_names):
            sl = self.proj.slot_for_part(name)
            base = (sl.base_color or None) if self.preview.backend == "gl" else (self.resolver(sl.base_color) if sl.base_color else None)
            out[i] = (self.proj.slot_effective(sl, state), base, sl.normal_strength)
        return out

    def set_stage_visible(self, idx, on):
        self.preview.opts.stages_on[idx] = on
        self.preview.request()

    def run_validation(self):
        self._validate_job = None
        self.bottom.show_issues(self.proj.validate())

    def goto_issue(self, loc: str):
        fname = loc.split(":", 1)[0]
        for i, sl in enumerate(self.proj.slots):
            hit = next((s for s in ALL_STATES if kit_filename(sl.name, s) == fname), None)
            if hit is not None:
                self.proj.set_active(i)
                self.target = TARGET_SHARED if hit == State.BASE else hit.value
                self.flush()
                break
        self.props.goto(loc)

    # ------------------------------------------------------------ части/выбор
    def _picked(self, idx):
        self.left.parts.select_part(idx)

    def on_part_selected(self, idx):
        self.preview.opts.highlight = idx
        mesh = self.preview.current_mesh()
        if 0 <= idx < len(mesh.part_names or []):
            i = self.proj.slot_index(self.proj.slot_for_part(mesh.part_names[idx]).name)
            if i != self.proj.active:
                self.proj.set_active(i)
                return
        self.preview.request()

    # ------------------------------------------------------------ проект
    def _confirm_discard(self) -> bool:
        if not self.proj.dirty:
            return True
        r = messagebox.askyesnocancel("Несохранённые изменения", "Сохранить изменения проекта?", parent=self.root)
        if r is None:
            return False
        return self.save_project() if r else True

    def _set_project(self, p: Project):
        self.proj.listeners.clear()
        self.proj = p
        p.listeners.append(self.on_project_changed)
        self.target = TARGET_SHARED
        self.rebuild_resolver()
        self.preview.mesh = None
        if p.model_path and Path(p.model_path).is_file():
            try:
                self._load_model_file(p.model_path)
            except Exception as e:
                self.log(f"Модель проекта не загружена: {e}")
        self.flush()

    def new_project(self):
        if self._confirm_discard():
            n = ask_text(self.root, "Новый проект", "Имя материала (основа имён файлов):", "mymaterial")
            if n and n.strip():
                self._set_project(Project(n.strip()))
                self.log(f"Новый проект: {n}")

    def open_project(self, path=None):
        if not self._confirm_discard():
            return
        path = path or filedialog.askopenfilename(parent=self.root, title="Открыть проект", initialdir=self.settings.last_dir or None, filetypes=[("Проект RVMAT Studio", "*.rvproj")])
        if path:
            try:
                self._set_project(Project.load(path))
                self.settings.add_recent(str(path)); self._fill_recent()
                self.log(f"Открыт {path}")
            except Exception as e:
                messagebox.showerror("Открытие", f"Не удалось открыть проект:\n{e}", parent=self.root)

    def save_project(self, as_new=False) -> bool:
        path = self.proj.path
        if as_new or not path:
            f = filedialog.asksaveasfilename(parent=self.root, title="Сохранить проект", defaultextension=".rvproj", initialfile=f"{self.proj.title}.rvproj", filetypes=[("Проект RVMAT Studio", "*.rvproj")])
            if not f:
                return False
            path = Path(f)
        try:
            self.proj.save(path)
            self.proj.clear_autosave()
            self.settings.add_recent(str(path)); self._fill_recent()
            self.log(f"Сохранён {path}")
            self.flush()
            return True
        except OSError as e:
            messagebox.showerror("Сохранение", str(e), parent=self.root)
            return False

    def _fill_recent(self):
        self.recent_menu.delete(0, "end")
        for r in self.settings.recent:
            self.recent_menu.add_command(label=r, command=lambda r=r: self.open_project(r))

    def import_rvmat(self, paths=None):
        paths = paths or filedialog.askopenfilenames(parent=self.root, title="Импорт RVMAT (можно выбрать несколько файлов комплекта)", filetypes=[("RVMAT", "*.rvmat")], initialdir=self.settings.last_dir or None)
        if paths:
            try:
                for m in self.proj.import_files(paths):
                    self.log(m)
            except Exception as e:
                messagebox.showerror("Импорт", f"Не удалось разобрать файл:\n{e}", parent=self.root)

    def export(self):
        ExportDialog(self.root, self)

    def undo(self):
        if not self.proj.undo():
            self.status.set("Нечего отменять")

    def redo(self):
        if not self.proj.redo():
            self.status.set("Нечего повторять")

    # ------------------------------------------------------------ правки
    def copy_stage(self):
        names = [s.value for s in ALL_STATES]
        a = ask_text(self.root, "Копировать стадию", "Из состояния:\n" + ", ".join(names), names[0])
        b = ask_text(self.root, "Копировать стадию", "В состояние:\n" + ", ".join(names), names[1]) if a in names else None
        st = ask_text(self.root, "Копировать стадию", "Номер стадии (1–7):", "1") if b in names else None
        if st and st.isdigit() and 1 <= int(st) <= 7:
            self.proj.copy_stage(State(a), State(b), int(st))

    def batch_dialog(self):
        from .dialogs import Modal
        top = Modal(self.root, "Пакетное редактирование RVMAT", 640, 420)
        files = tk.Listbox(top, bg=theme.INPUT, fg=theme.FG, selectbackground=theme.ACC, height=8); files.pack(fill="both", expand=True, padx=10, pady=8)
        ttk.Button(top, text="Добавить файлы…", command=lambda: [files.insert("end", f) for f in filedialog.askopenfilenames(parent=top, filetypes=[("RVMAT", "*.rvmat")])]).pack(anchor="w", padx=10)
        row = ttk.Frame(top); row.pack(fill="x", padx=10, pady=6)
        param = tk.StringVar(value="specularPower"); val = tk.StringVar()
        ttk.Combobox(row, textvariable=param, state="readonly", values=["specularPower", "PixelShaderID", "VertexShaderID", "ambient", "diffuse", "forcedDiffuse", "emmisive", "specular"]).pack(side="left")
        ttk.Entry(row, textvariable=val).pack(side="left", fill="x", expand=True, padx=6)
        log = tk.Text(top, height=5); theme.style_text(log); log.pack(fill="x", padx=10)
        def apply():
            n = 0
            for f in files.get(0, "end"):
                try:
                    m = Material.load(f); name, raw = param.get(), val.get().strip()
                    if name in ("PixelShaderID", "VertexShaderID"):
                        m.set_string(name, raw)
                    elif name == "specularPower":
                        m.set_scalar(name, float(raw))
                    else:
                        m.set_color(name, [float(x) for x in raw.split(",")])
                    m.save(f, backup=True); n += 1; log.insert("end", f"OK  {f}\n")
                except Exception as e:
                    log.insert("end", f"ERR {f}: {e}\n")
            log.insert("end", f"Готово: {n}/{files.size()}\n")
        ttk.Button(top, text="Применить ко всем (создаются .bak)", style="Accent.TButton", command=apply).pack(pady=8)

    # ------------------------------------------------------------ текстуры
    def add_textures(self, paths):
        added = 0
        for pth in paths:
            if str(pth) not in self.proj.textures:
                self.proj.textures.append(str(pth)); added += 1
        self.proj.dirty = True
        self.log(f"Добавлено текстур: {added}")
        self.flush()

    def assign_texture(self, stage: int, phys: Path):
        phys = Path(phys)
        if stage == 0:                                 # Base Color: только предпросмотр, RVMAT не меняется
            self.proj.set_base_color(str(phys))
            self.log(f"Base Color (превью) ← {phys.name}; в RVMAT базовый цвет не записывается")
            return
        game = None
        if self.proj.project_root:
            try:
                game = to_game_path(phys, self.proj.project_root)
            except ValueError:
                game = None
        if game is None:
            game = unique_game_path(phys, self.proj.tex_map)
            short = phys.with_suffix(".paa").name
            self.log(f"Файл вне корня проекта — в RVMAT записано «{game}»" + (f" (имя «{short}» уже занято другим файлом)" if game != short else "")
                     + ". Укажите корень проекта (Инструменты → Корень проекта), чтобы получить полный игровой путь.")
        self.proj.tex_map[game.lower()] = str(phys)
        if str(phys) not in self.proj.textures:
            self.proj.textures.append(str(phys))
        self.rebuild_resolver()
        self.proj.set_value(self.target, f"Stage{stage}.texture", game, f"Stage{stage}: {phys.name}")
        self.log(f"Stage{stage} ← {game}")

    def browse_assign(self, stage):
        f = filedialog.askopenfilename(parent=self.root, title=f"Текстура для Stage{stage}", filetypes=IMG_TYPES, initialdir=self.settings.last_dir or None)
        if f:
            self.assign_texture(stage, Path(f))

    def browse_base(self):
        f = filedialog.askopenfilename(parent=self.root, title="Base Color (_co) для предпросмотра", filetypes=IMG_TYPES, initialdir=self.settings.last_dir or None)
        if f:
            self.proj.set_base_color(f)

    def view_texture(self, path):
        TextureViewer(self.root, str(path))

    def view_stage_texture(self, stage):
        st = self.props.view_material().stage(stage)
        f = self.find_texture(st.texture) if st and st.texture else None
        if f:
            self.view_texture(f)
        else:
            messagebox.showinfo("Просмотр", "Файл текстуры не найден (процедурная или вне корня проекта).", parent=self.root)

    def set_project_root(self):
        d = filedialog.askdirectory(parent=self.root, title="Корень проекта / P: Drive (папка меша: <меш>.fbx + data)", initialdir=self.proj.project_root or self.settings.project_root or self.settings.p_drive or None)
        if not d:
            return
        if autoload.scan_folder(d) and self.load_project_folder(d, quiet=True):   # в папке лежит модель и/или data — подтягиваем всё сами
            return
        self.proj._change("Корень проекта")
        self.proj.project_root = d
        self.settings.project_root = d; self.settings.save()
        self.rebuild_resolver()
        self.proj._notify()
        self.log(f"Корень проекта: {d}")

    def load_project_folder(self, folder=None, quiet=False):
        """Загрузка из папки <меш>/(<меш>.fbx, data/): модель, текстуры, материалы, селекшены, пресеты, настройки экспорта."""
        folder = folder or filedialog.askdirectory(parent=self.root, title="Папка проекта: <меш> с файлом .fbx и папкой data",
                                                   initialdir=self.proj.project_root or self.settings.project_root or self.settings.last_dir or None)
        if not folder:
            return False
        cands = autoload.scan_folder(folder)
        if not cands:
            if not quiet:
                messagebox.showinfo("Папка проекта", "В папке не найдено ни модели (.fbx/.obj), ни папки data с RVMAT/текстурами.", parent=self.root)
            return False
        cand = cands[0]
        if len(cands) > 1:
            dlg = CandidateDialog(self.root, cands)
            self.root.wait_window(dlg)
            cand = dlg.result
            if cand is None:
                return False
        if not autoload.is_pristine(self.proj):          # не затираем чужую работу молча
            if not messagebox.askyesno("Папка проекта", f"Найдено: {cand.summary()}\n\nЗагрузить? Текущий проект будет заменён.", parent=self.root):
                return False
            if not self._confirm_discard():
                return False
        part_names = []
        if cand.model:
            try:
                part_names = list(self._load_model_file(str(cand.model)).part_names or [])
            except Exception as e:
                self.log(f"Модель {cand.model.name} не загружена: {e}")
        msgs = autoload.load_candidate(self.proj, cand, part_names)
        self.proj.path = None
        self.settings.project_root = self.proj.project_root
        self.settings.last_dir = str(cand.mesh_dir)
        self.settings.save()
        self.target = TARGET_SHARED
        self.rebuild_resolver()
        for m in msgs:
            self.log(m)
        self.flush()
        return True

    def convert_textures(self):
        if not self.settings.image_to_paa:
            messagebox.showinfo("PAA", "Укажите путь к ImageToPAA.exe в Настройках (нужны DayZ Tools на Windows).", parent=self.root)
            return
        ok = bad = 0
        for t in self.proj.textures:
            p = Path(t)
            if p.suffix.lower() in (".png", ".tga", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"):
                try:
                    self.log(f"PAA: {convert_to_paa(p, Path(self.settings.image_to_paa))}"); ok += 1
                except Exception as e:
                    self.log(f"Ошибка конвертации {p.name}: {e}"); bad += 1
        messagebox.showinfo("PAA", f"Готово: {ok}, ошибок: {bad}", parent=self.root)

    def open_workbench(self):
        st = self.settings
        try:
            _, gp = dayztools.check_in_workbench(st.workbench, st.gproj, st.p_drive, self.proj.project_root or st.project_root, st.dayz_tools)
            self.log(f"Workbench запущен с проектом {gp}")
            messagebox.showinfo("Workbench", f"Workbench запущен с проектом:\n{gp}\n\nОткройте материал в Resource Browser (сначала экспортируйте комплект внутрь P:/проекта).", parent=self.root)
        except dayztools.WorkbenchError as e:
            messagebox.showwarning("Workbench", str(e), parent=self.root)
        except OSError as e:
            messagebox.showwarning("Workbench", f"Не удалось запустить: {e}", parent=self.root)

    # ------------------------------------------------------------ пресеты
    def apply_preset(self, pid):
        pr = self.pm.items[pid]
        self.proj.apply_material(pr.material(), f"Пресет: {pr.name}", preset_tag=pr.id if pr.builtin else pr.name)
        self.log(f"Применён пресет «{pr.name}» (назначенные текстуры сохранены)")

    def save_as_preset(self):
        n = ask_text(self.root, "Новый пресет", "Название:")
        if not n or not n.strip():
            return
        c = ask_text(self.root, "Новый пресет", "Категория:\n" + ", ".join(CATEGORIES), CATEGORIES[0])
        self.pm.create(n.strip(), c if c in CATEGORIES else CATEGORIES[-1], "Пользовательский пресет", self.proj.spec.shared)
        self.left.presets.refresh()
        self.log(f"Пресет «{n}» сохранён")

    # ------------------------------------------------------------ предпросмотр
    def _load_model_file(self, f):
        mesh = fbx.load_fbx(f) if f.lower().endswith(".fbx") else meshes.load_obj(f)
        self.preview.mesh = mesh
        vals = list(self.shape_cb["values"])
        if "Своя модель" not in vals:
            self.shape_cb["values"] = vals + ["Своя модель"]
        self.shape.set("Своя модель"); self.preview.shape = "Своя модель"; self.preview.opts.highlight = -1
        return mesh

    def import_model(self, path=None):
        f = path or filedialog.askopenfilename(parent=self.root, title="Модель", filetypes=[("Модели", "*.obj *.fbx *.p3d"), ("Все", "*.*")], initialdir=self.settings.last_dir or None)
        if not f:
            return
        if Path(f).suffix.lower() not in (".obj", ".fbx"):
            messagebox.showinfo("Модель", "Поддерживаются OBJ и FBX. P3D (формат BI) напрямую не читается — экспортируйте модель в FBX/OBJ.", parent=self.root)
            return
        try:
            mesh = self._load_model_file(f)
        except Exception as e:
            messagebox.showwarning("Модель", f"Не удалось загрузить: {e}", parent=self.root)
            return
        self.proj.model_path = f
        self.proj.dirty = True
        self.settings.last_dir = str(Path(f).parent)
        names = mesh.part_names or []
        self.log(f"Загружена модель {Path(f).name}: {len(mesh.f)} треуг., частей: {len(names)}" + (f" ({', '.join(names[:6])}{'…' if len(names) > 6 else ''})" if names else ""))
        self.flush()

    def load_hdri(self):
        f = filedialog.askopenfilename(parent=self.root, title="HDRI / панорама", filetypes=[("Изображения", "*.png *.jpg *.jpeg *.bmp *.tga"), ("Все", "*.*")])
        if f:
            from PIL import Image
            im = Image.open(f).convert("RGB"); im.thumbnail((1024, 512))
            self.preview.hdri = (np.asarray(im).astype("float32") / 255) ** 2.2
            self.preview.request()
            self.log(f"Окружение: {Path(f).name}")

    def snapshot(self):
        f = filedialog.asksaveasfilename(parent=self.root, defaultextension=".png", initialfile="preview.png", filetypes=[("PNG", "*.png")])
        if f:
            from PIL import Image
            Image.fromarray(self.preview.render_sync()).save(f)

    def open_settings(self):
        d = SettingsDialog(self.root, self.settings)
        self.root.wait_window(d)
        if d.ok:
            self.settings.save()

    # ------------------------------------------------------------ служебное
    def _schedule_autosave(self):
        def tick():
            if self._closing:
                return
            try:
                self.proj.autosave()
            except OSError:
                pass
            self._auto_job = self.root.after(max(10, self.settings.autosave_seconds) * 1000, tick)
        self._auto_job = self.root.after(max(10, self.settings.autosave_seconds) * 1000, tick)

    def check_recovery(self):
        files = Project.recovery_files()
        if files and messagebox.askyesno("Восстановление", f"Найдено автосохранение:\n{files[-1].name}\nВосстановить?", parent=self.root):
            p = Project.load(files[-1])
            p.path, p.dirty = None, True
            self._set_project(p)

    def close(self):
        if not self._confirm_discard():
            return
        self._closing = True
        try:
            self.settings.geometry = self.root.geometry()
            self.settings.save()
        except OSError:
            pass
        self.proj.clear_autosave()
        self.preview.shutdown()
        self.root.destroy()


def main(argv=None) -> int:
    import sys
    if sys.platform == "win32":                       # чёткий текст на экранах с масштабом
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    app = StudioApp()
    app.root.after(300, app.check_recovery)
    app.root.mainloop()
    return 0
