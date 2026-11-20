from __future__ import annotations

import base64
from pathlib import Path

from PySide6.QtCore import QByteArray, Qt, QTimer
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDockWidget, QDoubleSpinBox, QFileDialog, QInputDialog, QLabel,
                               QMainWindow, QMessageBox, QPlainTextEdit, QPushButton, QTabWidget, QToolBar,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget, QListWidget, QHBoxLayout)

from .. import __version__
from ..backend import ALL_STATES, Severity, State, kit_filename
from ..core import export as ex
from ..core.fbx import load_fbx
from ..core.meshes import SHAPES, load_obj
from ..core.presets import CATEGORIES, PresetManager
from ..core.project import TARGET_DECOR, TARGET_SHARED, Project
from ..core.render import DIFFERENCES, LIGHTING, make_resolver
from ..core.settings import Settings
from ..core.textures import ThumbCache, convert_to_paa, to_game_path
from .dialogs import BatchDialog, ExportDialog, SettingsDialog, TextureViewer
from .library import PresetsTab, StatesTab, TexturesTab
from .preview import GLPreviewWidget, PreviewWidget
from .properties import PropertiesPanel

VIEWS = [("Итог", "final"), ("Base Color (_co)", "albedo"), ("AS: затенение ambient", "ao"), ("Нормали", "normal"), ("Specular (SMDI-R)", "specular"),
         ("Metal (SMDI-G)", "metal"), ("Gloss (SMDI-B)", "gloss"), ("Свечение (SMDI-A)", "emissive"),
         ("Detail", "detail"), ("UV-шашечки", "checker")]
SEV_TEXT = {Severity.ERROR: "Критическая", Severity.WARNING: "Предупреждение", Severity.INFO: "Инфо"}


class MainWindow(QMainWindow):
    def __init__(self, settings: Settings | None = None, preset_manager: PresetManager | None = None):
        super().__init__()
        self.settings = settings or Settings.load()
        self.pm = preset_manager or PresetManager()
        self.proj = Project()
        self.thumbs = ThumbCache()
        self.resolver = make_resolver(None)
        self.setWindowTitle("DayZ RVMAT Studio")
        self.resize(1500, 900)

        # центральная область
        self.preview = self._make_preview()
        center = QWidget()
        cl = QVBoxLayout(center)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.addWidget(self._build_preview_bar())
        cl.addWidget(self.preview, 1)
        self.setCentralWidget(center)

        # правая панель
        self.panel = PropertiesPanel(self.proj, lambda t: self.resolver.find(t), self.thumbs)
        self.dock_props = self._dock("Параметры материала", self.panel, Qt.RightDockWidgetArea, "props")
        self.dock_props.setMinimumWidth(520)

        # левая панель
        self.tab_tex = TexturesTab(self.proj, self.thumbs)
        self.tab_presets = PresetsTab(self.pm)
        self.tab_states = StatesTab(self.proj)
        tabs = QTabWidget()
        tabs.addTab(self.tab_tex, "Текстуры")
        tabs.addTab(self.tab_presets, "Пресеты")
        tabs.addTab(self.tab_states, "Состояния")
        self.left_tabs = tabs
        self.dock_lib = self._dock("Библиотека", tabs, Qt.LeftDockWidgetArea, "lib")
        self.dock_lib.setMinimumWidth(320)

        # нижняя панель
        self.issues = QTreeWidget()
        self.issues.setHeaderLabels(["Тип", "Код", "Сообщение", "Расположение"])
        self.issues.setColumnWidth(0, 110); self.issues.setColumnWidth(1, 60); self.issues.setColumnWidth(2, 520)
        self.issues.itemDoubleClicked.connect(self.goto_issue)
        self.logw = QPlainTextEdit(); self.logw.setReadOnly(True)
        self.histw = QListWidget()
        self.status_lbl = QLabel()
        bt = QTabWidget()
        bt.addTab(self.issues, "Ошибки и предупреждения")
        bt.addTab(self.logw, "Журнал")
        bt.addTab(self.histw, "История изменений")
        self.bottom_tabs = bt
        self.dock_bottom = self._dock("Диагностика", bt, Qt.BottomDockWidgetArea, "bottom")
        self.dock_bottom.setMinimumHeight(140)
        self.statusBar().addPermanentWidget(self.status_lbl)

        self._build_menus()
        self._wire()
        self._vtimer = QTimer(self, singleShot=True, interval=300, timeout=self.run_validation)
        self._atimer = QTimer(self, interval=self.settings.autosave_seconds * 1000, timeout=self._autosave)
        self._atimer.start()
        self.resizeDocks([self.dock_bottom], [190], Qt.Vertical)
        self.resizeDocks([self.dock_lib, self.dock_props], [330, 560], Qt.Horizontal)
        self._restore_layout()
        self.on_project_changed()
        self.log(f"DayZ RVMAT Studio {__version__} запущен")

    # ------------------------------------------------------------ построение
    def _dock(self, title, widget, area, name):
        d = QDockWidget(title, self)
        d.setObjectName(name)
        d.setWidget(widget)
        self.addDockWidget(area, d)
        return d

    def _build_preview_bar(self):
        box = QWidget()
        v = QVBoxLayout(box)
        v.setContentsMargins(4, 4, 4, 2)
        v.setSpacing(3)
        r1, r2 = QHBoxLayout(), QHBoxLayout()
        self.c_shape = QComboBox(); self.c_shape.addItems(list(SHAPES) + ["Своя модель"])
        self.c_light = QComboBox(); self.c_light.addItems(list(LIGHTING))
        self.c_view = QComboBox()
        for t, val in VIEWS:
            self.c_view.addItem(t, val)
        self.sp_tile = QDoubleSpinBox(); self.sp_tile.setRange(0.1, 32); self.sp_tile.setValue(1); self.sp_tile.setPrefix("Повтор ×"); self.sp_tile.setSingleStep(0.5)
        self.sl_light = QDoubleSpinBox(); self.sl_light.setRange(-180, 180); self.sl_light.setPrefix("Свет ∠"); self.sl_light.setSuffix("°")
        self.k_wire = QCheckBox("Сетка"); self.k_grid = QCheckBox("Решётка"); self.k_uv = QCheckBox("UV-развёртка")
        self.k_cmp = QCheckBox("Сравнить с:")
        self.c_cmp = QComboBox()
        for s in ALL_STATES:
            self.c_cmp.addItem(s.value, s.value)
        self.c_cmp.setCurrentIndex(1)
        for w_ in (QLabel("Модель"), self.c_shape, QLabel("Свет"), self.c_light, QLabel("Вид"), self.c_view, self.sp_tile, self.sl_light):
            r1.addWidget(w_)
        r1.addStretch()
        b_obj = QPushButton("Модель…"); b_obj.setToolTip("Импорт модели: OBJ или FBX (P3D — сначала конвертируйте в OBJ/FBX)")
        b_hdri = QPushButton("HDRI…"); b_hdri.setToolTip("Панорама окружения (PNG/JPG, equirectangular)")
        b_cam = QPushButton("⟲ Камера")
        b_shot = QPushButton("Снимок…")
        for b, f in ((b_obj, self.import_obj), (b_hdri, self.load_hdri), (b_cam, lambda: self.preview.reset_camera()), (b_shot, self.save_snapshot)):
            b.clicked.connect(f)
        for w_ in (self.k_wire, self.k_grid, self.k_uv, self.k_cmp, self.c_cmp):
            r2.addWidget(w_)
        r2.addStretch()
        for b in (b_obj, b_hdri, b_cam, b_shot):
            r2.addWidget(b)
        v.addLayout(r1); v.addLayout(r2)
        return box

    def _make_preview(self):
        """OpenGL, если доступен (и не отключён в настройках/переменной RVMAT_RENDERER), иначе CPU."""
        import os
        mode = os.environ.get("RVMAT_RENDERER") or self.settings.renderer
        w = None
        if mode in ("auto", "gl"):
            try:
                from ..core.gl_render import gl_available
                if gl_available():
                    w = GLPreviewWidget()
                    w.gl_failed.connect(self._swap_to_cpu)
            except Exception:
                w = None
        if w is None:
            w = PreviewWidget()
        w.status.connect(lambda s: self.statusBar().showMessage(s, 5000))
        return w

    def _swap_to_cpu(self, reason=""):
        old = self.preview
        if old.backend == "cpu":
            return
        new = PreviewWidget()
        new.opts, new.mesh, new.hdri, new.show_uv, new.uv_bg = old.opts, old.mesh, old.hdri, old.show_uv, old.uv_bg
        new.status.connect(lambda s: self.statusBar().showMessage(s, 5000))
        lay = self.centralWidget().layout()
        lay.replaceWidget(old, new)
        QTimer.singleShot(0, old.deleteLater)
        old.hide()
        self.preview = new
        self.log(f"OpenGL недоступен ({reason[:120]}), включён программный рендер")
        self.refresh_preview()

    def _act(self, menu, text, fn, shortcut=None):
        a = QAction(text, self)
        if shortcut:
            a.setShortcut(QKeySequence(shortcut))
        a.triggered.connect(lambda _=False: fn())
        menu.addAction(a)
        self.addAction(a)
        return a

    def _build_menus(self):
        mb = self.menuBar()
        m = mb.addMenu("&Файл")
        self._act(m, "Новый проект", self.new_project, "Ctrl+N")
        self._act(m, "Открыть проект…", self.open_project, "Ctrl+O")
        self.m_recent = m.addMenu("Недавние")
        self._act(m, "Сохранить", self.save_project, "Ctrl+S")
        self._act(m, "Сохранить как…", lambda: self.save_project(True), "Ctrl+Shift+S")
        m.addSeparator()
        self._act(m, "Импорт RVMAT (один или комплект)…", self.import_rvmat, "Ctrl+I")
        self.a_exp1 = self._act(m, "Экспорт выбранного состояния…", self.export_single)
        self._act(m, "Экспорт комплекта…", self.export_kit, "Ctrl+E")
        self._act(m, "Экспорт проекта (RVMAT + текстуры + .rvproj)…", self.export_project)
        m.addSeparator()
        self._act(m, "Выход", self.close, "Ctrl+Q")
        e = mb.addMenu("&Правка")
        self.a_undo = self._act(e, "Отменить", self.undo, "Ctrl+Z")
        self.a_redo = self._act(e, "Повторить", self.redo, "Ctrl+Y")
        e.addSeparator()
        self._act(e, "Копировать стадию в другое состояние…", self.copy_stage)
        self._act(e, "Переименовать материал…", self.rename, "F2")
        self._act(e, "Пакетное редактирование RVMAT…", lambda: BatchDialog(self).exec())
        self._act(e, "Фокус на поиск текстур", lambda: (self.left_tabs.setCurrentIndex(0), self.tab_tex.search.setFocus()), "Ctrl+F")
        v = mb.addMenu("&Вид")
        for d in (self.dock_lib, self.dock_props, self.dock_bottom):
            v.addAction(d.toggleViewAction())
        self._act(v, "Сбросить раскладку", self.reset_layout)
        t = mb.addMenu("&Инструменты")
        self._act(t, "Проверить (диагностика)", self.run_validation, "F5")
        self._act(t, "Корень проекта / P: Drive…", self.set_project_root)
        self._act(t, "Конвертировать текстуры в PAA (DayZ Tools)…", self.convert_textures)
        self._act(t, "Проверить в Workbench…", self.open_workbench)
        self._act(t, "Настройки…", self.open_settings, "Ctrl+,")
        h = mb.addMenu("&Справка")
        self._act(h, "Отличия предпросмотра от игры", self.show_differences)
        self._act(h, "О программе", lambda: QMessageBox.about(self, "DayZ RVMAT Studio", f"DayZ RVMAT Studio {__version__}\nГенератор и редактор RVMAT для DayZ."))

    def _wire(self):
        self.proj.listeners.append(self.on_project_changed)
        self.c_shape.currentTextChanged.connect(lambda t: (setattr(self.preview.opts, "shape", t), self.preview.request()))
        self.c_light.currentTextChanged.connect(lambda t: (setattr(self.preview.opts, "lighting", t), self.preview.request()))
        self.c_view.currentIndexChanged.connect(lambda: (setattr(self.preview.opts, "view", self.c_view.currentData()), self.preview.request()))
        self.sp_tile.valueChanged.connect(lambda x: (setattr(self.preview.opts, "tiling", x), self.preview.request()))
        self.sl_light.valueChanged.connect(lambda x: (setattr(self.preview.opts, "light_rot", x), self.preview.request()))
        self.k_wire.toggled.connect(lambda x: (setattr(self.preview.opts, "wire", x), self.preview.request()))
        self.k_grid.toggled.connect(lambda x: (setattr(self.preview.opts, "grid", x), self.preview.request()))
        self.k_uv.toggled.connect(lambda x: (setattr(self.preview, "show_uv", x), self.preview.request()))
        self.k_cmp.toggled.connect(lambda _: self.refresh_preview())
        self.c_cmp.currentIndexChanged.connect(lambda _: self.refresh_preview())
        self.panel.stage_visibility.connect(lambda i, on: (self.preview.opts.stages_on.__setitem__(i, on), self.preview.request()))
        self.panel.assign_requested.connect(self.browse_assign)
        self.panel.base_requested.connect(self.browse_base)
        self.panel.view_requested.connect(self.view_stage_texture)
        self.panel.target_changed.connect(lambda _: self.refresh_preview())
        self.tab_tex.assign.connect(lambda st, path: self.assign_texture(st, Path(path)))
        self.tab_tex.open_viewer.connect(lambda path: TextureViewer(path, self).exec())
        self.tab_presets.apply.connect(self.apply_preset)
        self.tab_presets.save_current.connect(self.save_as_preset)
        self.tab_states.selected.connect(self.panel.set_target)
        self.tab_states.toggled.connect(lambda s, on: self.proj.set_enabled(State(s), on))

    # ------------------------------------------------------------ состояние
    def log(self, msg: str):
        import time
        self.logw.appendPlainText(f"{time.strftime('%H:%M:%S')}  {msg}")
        self.statusBar().showMessage(msg, 4000)

    def rebuild_resolver(self):
        root = Path(self.proj.project_root) if self.proj.project_root else None
        self.resolver = make_resolver(root, mapping=self.proj.tex_map)

    def on_project_changed(self):
        self.panel.proj = self.proj
        self.panel.refresh()
        self.tab_states.refresh()
        self.tab_tex.reload()
        self.refresh_preview()
        self.histw.clear()
        self.histw.addItems(self.proj.history[-200:][::-1])
        self.a_undo.setEnabled(self.proj.can_undo)
        self.a_redo.setEnabled(self.proj.can_redo)
        self.setWindowTitle(f"{'*' if self.proj.dirty else ''}{self.proj.name} — DayZ RVMAT Studio")
        self.status_lbl.setText(f"{self.proj.name} · {len(self.proj.enabled)} файл(ов) в комплекте" + (" · не сохранён" if self.proj.dirty else ""))
        self._vtimer.start()

    def refresh_preview(self):
        st = self.panel.view_state()
        a = self.proj.effective(st)
        b = self.proj.effective(State(self.c_cmp.currentData())) if self.k_cmp.isChecked() else None
        s4 = a.stage(4)
        self.preview.uv_bg = self.resolver(self.proj.base_color) if self.proj.base_color else (self.resolver(s4.texture) if s4 and s4.texture else None)
        self.preview.opts.base_color = self.resolver(self.proj.base_color) if self.proj.base_color else None
        self.preview.set_materials(a, b, self.resolver)

    def run_validation(self):
        issues = self.proj.validate()
        self.issues.clear()
        colors = {Severity.ERROR: "#ff7b72", Severity.WARNING: "#e3b341", Severity.INFO: "#79c0ff"}
        from PySide6.QtGui import QColor
        for i in issues:
            it = QTreeWidgetItem([SEV_TEXT[i.severity], i.code, i.message, i.location])
            it.setForeground(0, QColor(colors[i.severity]))
            self.issues.addTopLevelItem(it)
        n = [sum(1 for i in issues if i.severity == s) for s in (Severity.ERROR, Severity.WARNING, Severity.INFO)]
        self.bottom_tabs.setTabText(0, f"Ошибки и предупреждения ({n[0]}/{n[1]}/{n[2]})")
        return issues

    def goto_issue(self, item, _=0):
        loc = item.text(3)
        fname, _, path = loc.partition(":")
        for s in ALL_STATES:
            if kit_filename(self.proj.name, s) == fname:
                self.panel.set_target(s.value if s != State.BASE else TARGET_SHARED)
                break
        self.panel.goto(loc)

    # ------------------------------------------------------------ файл
    def _confirm_discard(self) -> bool:
        if not self.proj.dirty:
            return True
        r = QMessageBox.question(self, "Несохранённые изменения", "Сохранить изменения проекта?",
                                 QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
        if r == QMessageBox.Save:
            return self.save_project()
        return r == QMessageBox.Discard

    def _set_project(self, p: Project):
        self.proj.listeners.clear()
        self.proj = p
        p.listeners.append(self.on_project_changed)
        self.tab_tex.proj = self.tab_states.proj = self.proj
        self.rebuild_resolver()
        self.on_project_changed()

    def new_project(self):
        if self._confirm_discard():
            name, ok = QInputDialog.getText(self, "Новый проект", "Имя материала (основа имён файлов):", text="mymaterial")
            if ok and name.strip():
                self._set_project(Project(name.strip()))
                self.log(f"Новый проект: {name}")

    def open_project(self, path=None):
        if not self._confirm_discard():
            return
        path = path or QFileDialog.getOpenFileName(self, "Открыть проект", self.settings.last_dir, "Проект RVMAT Studio (*.rvproj)")[0]
        if path:
            try:
                self._set_project(Project.load(path))
                self.settings.add_recent(str(path)); self._fill_recent()
                self.log(f"Открыт {path}")
            except Exception as e:
                QMessageBox.critical(self, "Открытие", f"Не удалось открыть проект:\n{e}")

    def save_project(self, as_new=False) -> bool:
        path = self.proj.path
        if as_new or not path:
            f, _ = QFileDialog.getSaveFileName(self, "Сохранить проект", f"{self.proj.name}.rvproj", "Проект RVMAT Studio (*.rvproj)")
            if not f:
                return False
            path = Path(f)
        try:
            self.proj.save(path)
            self.proj.clear_autosave()
            self.settings.add_recent(str(path)); self._fill_recent()
            self.log(f"Сохранён {path}")
            self.on_project_changed()
            return True
        except OSError as e:
            QMessageBox.critical(self, "Сохранение", str(e))
            return False

    def _fill_recent(self):
        self.m_recent.clear()
        for r in self.settings.recent:
            self.m_recent.addAction(r, lambda r=r: self.open_project(r))

    def import_rvmat(self, paths=None):
        paths = paths or QFileDialog.getOpenFileNames(self, "Импорт RVMAT (можно выбрать несколько файлов комплекта)", self.settings.last_dir, "RVMAT (*.rvmat)")[0]
        if paths:
            try:
                for m in self.proj.import_files(paths):
                    self.log(m)
            except Exception as e:
                QMessageBox.critical(self, "Импорт", f"Не удалось разобрать файл:\n{e}")

    def _export_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Папка экспорта", self.proj.export_dir or self.settings.last_dir)
        if d:
            self.proj.export_dir = d
        return d

    def _do_export(self, plan):
        dlg = ExportDialog(plan, self)
        if dlg.exec():
            try:
                w = ex.execute(plan, overwrite=dlg.over.isChecked())
                self.log(f"Экспортировано файлов: {len(w)}")
                QMessageBox.information(self, "Экспорт", f"Записано файлов: {len(w)}")
            except Exception as e:
                QMessageBox.critical(self, "Экспорт", str(e))

    def export_kit(self):
        d = self._export_dir()
        if d:
            self._do_export(ex.plan_kit(self.proj, d))

    def export_single(self):
        d = self._export_dir()
        if d:
            self._do_export(ex.plan_single(self.proj, self.panel.view_state(), d))

    def export_project(self):
        d = self._export_dir()
        if not d:
            return
        plan = ex.plan_kit(self.proj, d)
        dlg = ExportDialog(plan, self)
        if dlg.exec():
            try:
                w = ex.export_project(self.proj, d, overwrite=dlg.over.isChecked())
                self.log(f"Экспорт проекта: {len(w)} файл(ов)")
            except Exception as e:
                QMessageBox.critical(self, "Экспорт", str(e))

    # ------------------------------------------------------------ правка
    def undo(self):
        if not self.proj.undo():
            self.statusBar().showMessage("Нечего отменять", 2000)

    def redo(self):
        if not self.proj.redo():
            self.statusBar().showMessage("Нечего повторять", 2000)

    def rename(self):
        n, ok = QInputDialog.getText(self, "Имя материала", "Имя:", text=self.proj.name)
        if ok and n.strip():
            self.proj.rename(n.strip())

    def copy_stage(self):
        names = [s.value for s in ALL_STATES]
        a, ok = QInputDialog.getItem(self, "Копировать стадию", "Из состояния:", names, 0, False)
        if not ok: return
        b, ok = QInputDialog.getItem(self, "Копировать стадию", "В состояние:", names, 1, False)
        if not ok: return
        st, ok = QInputDialog.getInt(self, "Копировать стадию", "Номер стадии (1–7):", 1, 1, 7)
        if ok:
            self.proj.copy_stage(State(a), State(b), st)

    # ------------------------------------------------------------ текстуры
    def assign_texture(self, stage: int, phys: Path):
        if stage == 0:                                # Base Color: только предпросмотр, RVMAT не меняется
            self.proj.set_base_color(str(phys))
            self.log(f"Base Color (превью) ← {phys.name}; в RVMAT базовый цвет не записывается")
            return
        root = Path(self.proj.project_root) if self.proj.project_root else None
        game = None
        if root:
            try:
                game = to_game_path(phys, root)
            except ValueError:
                game = None
        if game is None:
            game = phys.with_suffix(".paa").name
            self.proj.tex_map[game.lower()] = str(phys)
            self.log(f"Файл вне корня проекта — в RVMAT записано «{game}». Укажите корень проекта (Инструменты → Корень проекта), чтобы получить полный игровой путь.")
        else:
            self.proj.tex_map[game.lower()] = str(phys)
        if str(phys) not in self.proj.textures:
            self.proj.textures.append(str(phys))
        self.rebuild_resolver()
        self.proj.set_value(self.panel.target_id, f"Stage{stage}.texture", game, f"Stage{stage}: {phys.name}")
        self.log(f"Stage{stage} ← {game}")

    def browse_assign(self, stage: int):
        f, _ = QFileDialog.getOpenFileName(self, f"Текстура для Stage{stage}", self.settings.last_dir, "Текстуры (*.paa *.png *.tga *.jpg *.jpeg *.tif *.tiff *.bmp)")
        if f:
            self.assign_texture(stage, Path(f))

    def browse_base(self):
        f, _ = QFileDialog.getOpenFileName(self, "Base Color (_co) для предпросмотра", self.settings.last_dir, "Текстуры (*.paa *.png *.tga *.jpg *.jpeg *.tif *.tiff *.bmp)")
        if f:
            self.proj.set_base_color(f)

    def view_stage_texture(self, stage: int):
        s = self.panel.view_material().stage(stage)
        f = self.resolver.find(s.texture) if s and s.texture else None
        if f:
            TextureViewer(str(f), self).exec()
        else:
            QMessageBox.information(self, "Просмотр", "Файл текстуры не найден (процедурная или вне корня проекта).")

    def set_project_root(self):
        d = QFileDialog.getExistingDirectory(self, "Корень проекта / P: Drive", self.proj.project_root or self.settings.project_root or self.settings.p_drive)
        if d:
            self.proj._change("Корень проекта")
            self.proj.project_root = d
            self.rebuild_resolver()
            self.proj._notify()
            self.log(f"Корень проекта: {d}")

    def convert_textures(self):
        if not self.settings.image_to_paa:
            QMessageBox.information(self, "PAA", "Укажите путь к ImageToPAA.exe в Настройках (нужны DayZ Tools на Windows).")
            return
        ok = bad = 0
        for t in self.proj.textures:
            p = Path(t)
            if p.suffix.lower() in (".png", ".tga", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"):
                try:
                    out = convert_to_paa(p, Path(self.settings.image_to_paa))
                    self.log(f"PAA: {out}")
                    ok += 1
                except Exception as e:
                    self.log(f"Ошибка конвертации {p.name}: {e}")
                    bad += 1
        QMessageBox.information(self, "PAA", f"Готово: {ok}, ошибок: {bad}")

    def open_workbench(self):
        if not self.proj.export_dir:
            QMessageBox.information(self, "Workbench", "Сначала экспортируйте комплект.")
            return
        try:
            ex.check_in_workbench(self.settings.workbench, Path(self.proj.export_dir) / kit_filename(self.proj.name, self.panel.view_state()))
        except Exception as e:
            QMessageBox.warning(self, "Workbench", str(e))

    # ------------------------------------------------------------ пресеты
    def apply_preset(self, pid: str):
        p = self.pm.items[pid]
        self.proj.apply_material(p.material(), f"Пресет: {p.name}")
        self.log(f"Применён пресет «{p.name}» (назначенные текстуры сохранены)")

    def save_as_preset(self):
        n, ok = QInputDialog.getText(self, "Новый пресет", "Название:")
        if not ok or not n.strip():
            return
        c, ok = QInputDialog.getItem(self, "Новый пресет", "Категория:", CATEGORIES, 0, False)
        if ok:
            self.pm.create(n.strip(), c, "Пользовательский пресет", self.proj.spec.shared)
            self.tab_presets.reload()
            self.log(f"Пресет «{n}» сохранён")

    # ------------------------------------------------------------ предпросмотр
    def import_obj(self):
        f, _ = QFileDialog.getOpenFileName(self, "Модель", self.settings.last_dir, "Модели (*.obj *.fbx *.p3d)")
        if not f:
            return
        ext = Path(f).suffix.lower()
        if ext not in (".obj", ".fbx"):
            QMessageBox.information(self, "Модель", "Поддерживаются OBJ и FBX. P3D (формат BI) напрямую не читается — экспортируйте модель в FBX/OBJ.")
            return
        try:
            self.preview.mesh = load_fbx(f) if ext == ".fbx" else load_obj(f)
            self.c_shape.setCurrentText("Своя модель")
            self.preview.opts.shape = "Своя модель"
            self.preview.request()
            self.log(f"Загружена модель {Path(f).name}: {len(self.preview.mesh.f)} треуг.")
        except Exception as e:
            QMessageBox.warning(self, "Модель", f"Не удалось загрузить: {e}")

    def load_hdri(self):
        f, _ = QFileDialog.getOpenFileName(self, "HDRI / панорама", self.settings.last_dir, "Изображения (*.png *.jpg *.jpeg *.tga *.bmp)")
        if f:
            from PIL import Image
            import numpy as np
            im = Image.open(f).convert("RGB")
            im.thumbnail((1024, 512))
            self.preview.hdri = (np.asarray(im).astype("float32") / 255) ** 2.2
            self.preview.request()
            self.log(f"Окружение: {Path(f).name}")

    def save_snapshot(self):
        f, _ = QFileDialog.getSaveFileName(self, "Снимок предпросмотра", "preview.png", "PNG (*.png)")
        if f:
            from PIL import Image
            Image.fromarray(self.preview.render_sync()).save(f)

    def show_differences(self):
        QMessageBox.information(self, "Отличия от игры", "\n\n".join("• " + d for d in DIFFERENCES))

    def open_settings(self):
        if SettingsDialog(self.settings, self).exec():
            self.settings.save()
            self._atimer.setInterval(self.settings.autosave_seconds * 1000)

    # ------------------------------------------------------------ служебное
    def _autosave(self):
        try:
            self.proj.autosave()
        except OSError:
            pass

    def check_recovery(self):
        files = Project.recovery_files()
        if files and QMessageBox.question(self, "Восстановление", f"Найдено автосохранение:\n{files[-1].name}\nВосстановить?") == QMessageBox.Yes:
            p = Project.load(files[-1])
            p.path = None
            p.dirty = True
            self._set_project(p)

    def _restore_layout(self):
        self._default_state = self.saveState()
        self._fill_recent()
        try:
            if self.settings.geometry:
                self.restoreGeometry(QByteArray(base64.b64decode(self.settings.geometry)))
            if self.settings.window_state:
                self.restoreState(QByteArray(base64.b64decode(self.settings.window_state)))
        except Exception:
            pass

    def reset_layout(self):
        self.restoreState(self._default_state)
        for d in (self.dock_lib, self.dock_props, self.dock_bottom):
            d.show()

    def closeEvent(self, e):
        if not self._confirm_discard():
            e.ignore()
            return
        self.settings.geometry = base64.b64encode(bytes(self.saveGeometry())).decode()
        self.settings.window_state = base64.b64encode(bytes(self.saveState())).decode()
        try:
            self.settings.save()
        except OSError:
            pass
        self.proj.clear_autosave()
        self.preview.shutdown()
        e.accept()
