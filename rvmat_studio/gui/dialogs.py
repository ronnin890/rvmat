from __future__ import annotations

from pathlib import Path

import numpy as np
import threading

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout,
                               QLabel, QLineEdit, QListWidget, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QScrollArea,
                               QSpinBox, QVBoxLayout, QWidget)

from ..backend import Material, State
from ..core import dayztools
from ..core import export as ex
from ..core.logs import log_exc
from ..core.settings import Settings
from ..core.textures import channel_view, load_rgba, normal_report, probe
from .widgets import pixmap_from_rgba


STATE_TITLES = {"base": "Основной (name.rvmat)", "damage": "Повреждённый (_damage)", "destruct": "Разрушенный (_destruct)",
                "decorative": "Декоративный (_decorative)", "decorative_damage": "Декоративный повреждённый", "decorative_destruct": "Декоративный разрушенный"}


class TextureViewer(QDialog):
    """Каналы RGB/A, нормали, SMDI, сравнение двух текстур."""
    MODES = [("Цвет (RGB)", "rgb"), ("С альфой", "rgba"), ("R", "r"), ("G", "g"), ("B", "b"), ("Alpha", "a"),
             ("Normal: инверсия G", "swapg")]

    def __init__(self, path: str, parent=None, other: str = ""):
        super().__init__(parent)
        self.setWindowTitle(f"Просмотр — {Path(path).name}")
        self.resize(860, 640)
        self.path, self.other = path, other
        v = QVBoxLayout(self)
        r = QHBoxLayout()
        self.mode = QComboBox()
        for t, m in self.MODES:
            self.mode.addItem(t, m)
        self.mode.currentIndexChanged.connect(self.draw)
        self.cmp = QComboBox(); self.cmp.addItem("Сравнить с: —", "")
        r.addWidget(QLabel("Канал:")); r.addWidget(self.mode); r.addWidget(self.cmp, 1)
        b = QPushButton("Выбрать файл для сравнения…"); b.clicked.connect(self.pick_other); r.addWidget(b)
        v.addLayout(r)
        self.img = QLabel(alignment=Qt.AlignCenter)
        sc = QScrollArea(); sc.setWidget(self.img); sc.setWidgetResizable(True)
        v.addWidget(sc, 1)
        self.info = QPlainTextEdit(); self.info.setReadOnly(True); self.info.setMaximumHeight(130)
        v.addWidget(self.info)
        self.draw()

    def pick_other(self):
        f, _ = QFileDialog.getOpenFileName(self, "Сравнить с")
        if f:
            self.other = f
            self.draw()

    def _load(self, p):
        arr, fmt = load_rgba(p)
        return arr, fmt

    def draw(self):
        try:
            arr, fmt = self._load(self.path)
        except Exception as e:
            self.info.setPlainText(f"Ошибка: {e}")
            return
        mode = self.mode.currentData()
        view = arr if mode == "rgba" else channel_view(arr, mode)
        if self.other:
            try:
                o, _ = self._load(self.other)
                h = min(view.shape[0], o.shape[0], 512)
                from PIL import Image
                rs = lambda a: np.asarray(Image.fromarray(a).resize((int(a.shape[1] * h / a.shape[0]), h)))
                ov = rs(arr if mode == "rgba" else channel_view(o, mode)) if False else rs(channel_view(o, mode) if mode != "rgba" else o)
                view = np.concatenate([rs(view), ov], 1)
            except Exception as e:
                self.info.setPlainText(f"Сравнение недоступно: {e}")
        self.img.setPixmap(pixmap_from_rgba(view, 700))
        ti = probe(self.path)
        lines = [f"{ti.path}", f"Разрешение: {ti.width}×{ti.height} · формат {ti.fmt} · альфа: {'да' if ti.has_alpha else 'нет'}"]
        lines += [f"⚠ {p}" for p in ti.problems()]
        lines.append("Цветовое пространство: данные берутся как есть (sRGB-подобные); "
                     f"средний RGB = {tuple(int(x) for x in arr[:, :, :3].reshape(-1, 3).mean(0))}")
        n = normal_report(arr)
        lines.append(f"Проверка нормалей: средняя длина {n['mean_len']:.2f}, доля Z<0: {n['z_negative_ratio']:.1%}, "
                     f"средн. XY=({n['mean_xy'][0]:+.2f},{n['mean_xy'][1]:+.2f}) — применимо только к *_nohq")
        if self.path.lower().endswith(".paa") and "_no" in Path(self.path).stem.lower():
            lines.append("ℹ _nohq из PAA: применена обратная перестановка каналов (X нормали хранится инвертированным в альфе) — "
                         "без неё нормаль выглядит голубой вместо сине-фиолетовой.")
        if self.other:
            lines.append(f"Сравнение с: {self.other}")
        self.info.setPlainText("\n".join(lines))


class SettingsDialog(QDialog):
    def __init__(self, s: Settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Настройки и интеграция с DayZ Tools")
        self.s = s
        self.resize(620, 320)
        f = QFormLayout(self)
        self.e = {}
        for key, label in (("dayz_tools", "DayZ Tools (папка)"), ("workbench", "Workbench (exe)"),
                           ("image_to_paa", "ImageToPAA (exe)"), ("gproj", "Файл проекта dayz.gproj (Workbench)"), ("p_drive", "P: Drive"), ("project_root", "Корень проекта по умолчанию")):
            row = QHBoxLayout()
            ed = QLineEdit(getattr(s, key))
            b = QPushButton("…")
            b.setFixedWidth(30)
            b.clicked.connect(lambda _=0, ed=ed, k=key: self._browse(ed, k))
            row.addWidget(ed); row.addWidget(b)
            f.addRow(label, row)
            self.e[key] = ed
        self.renderer = QComboBox()
        for t, v in (("Авто (OpenGL, если доступен)", "auto"), ("Только OpenGL", "gl"), ("Только процессор (CPU)", "cpu")):
            self.renderer.addItem(t, v)
        self.renderer.setCurrentIndex(max(0, self.renderer.findData(s.renderer)))
        f.addRow("Рендер предпросмотра (нужен перезапуск)", self.renderer)
        self.full_paths = QCheckBox("Писать в RVMAT полный путь с диском (P:\\MyMod\\…)")
        self.full_paths.setChecked(s.full_drive_paths)
        f.addRow(self.full_paths)
        self.auto = QSpinBox(); self.auto.setRange(10, 3600); self.auto.setValue(s.autosave_seconds); self.auto.setSuffix(" с")
        f.addRow("Автосохранение", self.auto)
        find = QPushButton("Найти DayZ Tools автоматически"); find.clicked.connect(self._find)
        f.addRow(find)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject)
        f.addRow(bb)

    def _browse(self, ed, key):
        if key in ("workbench", "image_to_paa", "gproj"):
            p, _ = QFileDialog.getOpenFileName(self, "Файл", ed.text())
        else:
            p = QFileDialog.getExistingDirectory(self, "Папка", ed.text())
        if p:
            ed.setText(p)

    def _find(self):
        ok = self.s.find_tools()
        for k, ed in self.e.items():
            ed.setText(getattr(self.s, k))
        if not ok:
            QMessageBox.information(self, "DayZ Tools", "Не найдено в стандартных папках Steam. Укажите путь вручную.")

    def accept(self):
        for k, ed in self.e.items():
            setattr(self.s, k, ed.text().strip())
        self.s.autosave_seconds = self.auto.value()
        self.s.full_drive_paths = self.full_paths.isChecked()
        self.s.renderer = self.renderer.currentData()
        super().accept()


class ExportDialog(QDialog):
    """Одна кнопка «Экспорт»: путь, имя меша, что экспортировать, какие RVMAT — как в диалоге экспорта Blender."""
    _progress = Signal(int, int, object)
    _finished = Signal(object)

    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.setWindowTitle("Экспорт")
        self.resize(940, 660)
        p, st = win.proj, win.settings
        o = p.export_opts
        self._plan = None
        self._running = False
        self._prefix_manual = o.get("prefix") is not None
        self.last = None
        h = QHBoxLayout(self)
        left, right = QVBoxLayout(), QVBoxLayout()
        h.addLayout(left, 0); h.addLayout(right, 1)

        left.addWidget(QLabel("<b>Папка проекта (корень)</b>"))
        r = QHBoxLayout()
        self.root = QLineEdit(o.get("out_root") or p.project_root or st.project_root or "")
        b = QPushButton("Обзор…"); b.clicked.connect(self.browse)
        r.addWidget(self.root, 1); r.addWidget(b)
        left.addLayout(r)
        left.addWidget(QLabel("<b>Имя меша</b> (папка и имена файлов)"))
        self.mesh = QLineEdit(p.mesh_name or ex.default_mesh_name(p))
        left.addWidget(self.mesh)
        left.addWidget(QLabel("<b>Префикс игровых путей в RVMAT</b>"))
        self.prefix = QLineEdit(o.get("prefix") if o.get("prefix") is not None else "")
        left.addWidget(self.prefix)
        hint = QLabel("путь корня относительно диска P:, напр. MyMod"); hint.setStyleSheet("color:#8b949e")
        left.addWidget(hint)
        self.drive = QCheckBox("Полный путь с диском в RVMAT (P:\\MyMod\\…)")
        self.drive.setChecked(bool(o.get("drive_paths", st.full_drive_paths)))
        self.drive.setToolTip("Пути текстур в RVMAT: P:\\<префикс>\\<меш>\\data\\файл.paa.\nСтандартный формат DayZ — без диска; отключите, если игра/Binarize не находит файлы.")
        left.addWidget(self.drive)

        g = QGroupBox("Что экспортировать"); gl = QVBoxLayout(g)
        self.tex = QCheckBox("Текстуры (PNG/TGA → PAA)"); self.tex.setChecked(o.get("textures", True))
        self.conv = QComboBox(); self.conv.addItem("Встроенный конвертер", "builtin"); self.conv.addItem("DayZ Tools (ImageToPAA)", "tool")
        if o.get("converter") == "tool" and st.image_to_paa:
            self.conv.setCurrentIndex(1)
        if not st.image_to_paa:
            self.conv.model().item(1).setEnabled(False)
        self.fbx = QCheckBox("Меш FBX (модель проекта)"); self.fbx.setChecked(bool(o.get("fbx", True)) and bool(p.model_path)); self.fbx.setEnabled(bool(p.model_path))
        for w_ in (self.tex, self.conv, self.fbx):
            gl.addWidget(w_)
        left.addWidget(g)
        rv = QGroupBox("Какие RVMAT создавать"); rl = QVBoxLayout(rv)
        sel = set(o.get("states", ex.DEFAULT_STATES))
        self.states = {}
        for s_ in State:
            c = QCheckBox(STATE_TITLES[s_.value]); c.setChecked(s_.value in sel)
            self.states[s_.value] = c; rl.addWidget(c)
        left.addWidget(rv)
        note = QLabel("Для каждого материала/селекшена проекта создаётся свой набор."); note.setWordWrap(True); note.setStyleSheet("color:#8b949e")
        left.addWidget(note); left.addStretch()

        right.addWidget(QLabel("<b>Будет создано</b>"))
        self.tree = QPlainTextEdit(); self.tree.setReadOnly(True); self.tree.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.tree.setStyleSheet("font-family: Consolas, monospace;")
        right.addWidget(self.tree, 3)
        right.addWidget(QLabel("<b>Замечания</b>"))
        self.notes = QPlainTextEdit(); self.notes.setReadOnly(True)
        right.addWidget(self.notes, 1)
        self.over = QCheckBox("Перезаписать существующие файлы (будут созданы .bak)")
        right.addWidget(self.over)
        bot = QHBoxLayout()
        self.bar = QProgressBar(); self.bar.setTextVisible(False)
        self.status = QLabel("")
        self.btn_open = QPushButton("Открыть папку"); self.btn_open.clicked.connect(self.open_out); self.btn_open.hide()
        self.btn_close = QPushButton("Отмена"); self.btn_close.clicked.connect(self.close)
        self.go = QPushButton("Экспортировать"); self.go.setObjectName("accent"); self.go.setDefault(True); self.go.clicked.connect(self.run)
        for w_ in (self.bar, self.status, self.btn_open, self.btn_close, self.go):
            bot.addWidget(w_)
        bot.setStretch(0, 1)
        right.addLayout(bot)

        self._timer = QTimer(self, singleShot=True, interval=250, timeout=self.update_plan)
        self.root.textChanged.connect(self._on_edit); self.mesh.textChanged.connect(self._on_edit)
        self.prefix.textEdited.connect(self._on_prefix)
        for w_ in (self.drive, self.tex, self.fbx, *self.states.values()):
            w_.toggled.connect(lambda _=False: self._timer.start())
        self.conv.currentIndexChanged.connect(lambda _=0: self._timer.start())
        self._progress.connect(self._on_progress)
        self._finished.connect(self._finish)
        if not self._prefix_manual:
            self._auto_prefix()
        self.update_plan()

    # ---- настройки -> план
    def opts(self) -> dict:
        st = self.win.settings
        return {"out_root": self.root.text().strip(), "mesh_name": self.mesh.text().strip(), "prefix": self.prefix.text(),
                "textures": self.tex.isChecked(), "fbx": self.fbx.isChecked(), "drive_paths": self.drive.isChecked(), "converter": self.conv.currentData(),
                "states": [k for k, c in self.states.items() if c.isChecked()], "p_drive": st.p_drive, "image_to_paa": st.image_to_paa}

    def _auto_prefix(self):
        r = self.root.text().strip()
        self.prefix.blockSignals(True)
        self.prefix.setText(ex.default_prefix(r, self.win.settings.p_drive) if r else "")
        self.prefix.blockSignals(False)
        self._prefix_manual = False

    def _on_prefix(self, _=""):
        self._prefix_manual = True
        self._timer.start()

    def _on_edit(self, _=""):
        if not self._prefix_manual:
            self._auto_prefix()
        self._timer.start()

    def browse(self):
        d = QFileDialog.getExistingDirectory(self, "Папка проекта", self.root.text() or self.win.settings.p_drive)
        if d:
            self.root.setText(d)

    def update_plan(self):
        o = self.opts()
        self.tree.clear(); self.notes.clear()
        try:
            if not o["out_root"]:
                raise ValueError("Укажите папку проекта")
            if not o["states"]:
                raise ValueError("Выберите хотя бы один RVMAT")
            if not o["mesh_name"]:
                raise ValueError("Укажите имя меша")
            self._plan = plan = ex.plan_export(self.win.proj, o)
        except Exception as e:
            log_exc("gui.dialogs.update_plan")
            self._plan = None
            self.notes.setPlainText(f"⛔ {e}")
            self.go.setEnabled(False)
            return
        self.go.setEnabled(not self._running)
        out = Path(plan.out_dir)
        lines = [f"{Path(o['out_root'])}", f"└─ {out.name}/"]
        for x in (x for x in plan.ops if x.kind == "fbx"):
            lines.append(f"   ├─ {Path(x.dst).name}   [{x.note}]" + ("   ⚠ перезапись" if Path(x.dst).exists() else ""))
        lines.append("   └─ data/")
        for x in plan.ops:
            if x.kind != "fbx":
                lines.append(f"        {Path(x.dst).name}" + (f"   [{x.note}]" if x.kind == "texture" else "") + ("   ⚠ перезапись" if Path(x.dst).exists() else ""))
        self.tree.setPlainText("\n".join(lines))
        self.over.setEnabled(bool(plan.existing))
        if not plan.existing:
            self.over.setChecked(False)
        notes = ["ℹ " + n for n in plan.notes] + [f"[{i.code}] {i.location}: {i.message}" for i in plan.issues[:60]]
        if plan.existing:
            notes.append(f"Существующих файлов: {len(plan.existing)}")
        self.notes.setPlainText("\n".join(notes) or "Замечаний нет")
        self.status.setText(f"{len(plan.ops)} файл(ов), текстур: {sum(1 for x in plan.ops if x.kind == 'texture')}")

    # ---- выполнение
    def run(self):
        if self._plan is None or self._running:
            return
        if self._plan.existing and not self.over.isChecked():
            QMessageBox.warning(self, "Экспорт", "Некоторые файлы уже существуют. Отметьте перезапись или выберите другую папку.")
            return
        if any(o.mode == "convert-tool" for o in self._plan.ops) and not self.win.confirm_tool("image_to_paa"):
            return
        self._running = True
        self.go.setEnabled(False)
        plan, ow, tool = self._plan, self.over.isChecked(), self.win.settings.image_to_paa
        self.bar.setRange(0, len(plan.ops)); self.bar.setValue(0)

        def work():
            res = ex.run_export(plan, ow, tool, progress=lambda i, n, op: self._progress.emit(i, n, op))
            self._finished.emit(res)
        threading.Thread(target=work, daemon=True).start()

    def _on_progress(self, i, n, op):
        self.bar.setValue(i)
        self.status.setText(f"{i + 1}/{n}: {Path(op.dst).name}")

    def _finish(self, res):
        self._running = False
        self.bar.setValue(self.bar.maximum())
        o = self.opts()
        p, st = self.win.proj, self.win.settings
        p.export_opts = {k: v for k, v in o.items() if k not in ("image_to_paa", "p_drive")}
        p.mesh_name = self.mesh.text().strip()
        if not p.project_root:
            p.project_root = o["out_root"]
            self.win.rebuild_resolver()
        st.project_root = o["out_root"]
        try:
            st.save()
        except OSError:
            log_exc("gui.dialogs._finish")
        p.dirty = True
        lines = [f"Готово: записано {len(res.written)} файл(ов)."] + ["ℹ " + n for n in res.notes] + ["✗ " + e for e in res.errors]
        self.notes.setPlainText("\n".join(lines))
        self.status.setText(f"Записано: {len(res.written)}, ошибок: {len(res.errors)}")
        self.win.log(f"Экспорт в {self._plan.out_dir}: записано {len(res.written)}, ошибок {len(res.errors)}")
        self.win.on_project_changed()
        self.btn_open.show()
        self.btn_close.setText("Закрыть")
        self.last = res
        self._plan = None
        self.go.setEnabled(True)

    def open_out(self):
        o = self.opts()
        dayztools.open_folder(str(Path(o["out_root"]) / (o["mesh_name"] or "")))

    def reject(self):
        if not self._running:
            super().reject()

    def closeEvent(self, e):
        e.ignore() if self._running else e.accept()


class CandidateDialog(QDialog):
    """Выбор папки меша, если в выбранной папке их несколько."""
    def __init__(self, cands, parent=None):
        super().__init__(parent)
        self.setWindowTitle("В папке найдено несколько проектов")
        self.resize(640, 340)
        self.cands, self.result = cands, None
        v = QVBoxLayout(self)
        v.addWidget(QLabel("<b>Выберите, что загрузить:</b>"))
        self.lb = QListWidget()
        for c in cands:
            self.lb.addItem(f"{c.mesh_dir.name}   —   {c.summary()}")
        self.lb.setCurrentRow(0)
        self.lb.itemDoubleClicked.connect(lambda _: self.accept())
        v.addWidget(self.lb, 1)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Загрузить")
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject)
        v.addWidget(bb)

    def accept(self):
        r = self.lb.currentRow()
        if r >= 0:
            self.result = self.cands[r]
        super().accept()


class BatchDialog(QDialog):
    """Пакетное изменение параметра в нескольких готовых .rvmat (с резервной копией)."""
    PARAMS = ["specularPower", "PixelShaderID", "VertexShaderID", "ambient", "diffuse", "forcedDiffuse", "emmisive", "specular"]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Пакетное редактирование RVMAT")
        self.resize(640, 420)
        v = QVBoxLayout(self)
        self.files = QListWidget()
        v.addWidget(self.files, 1)
        b = QPushButton("Добавить файлы…"); b.clicked.connect(self.add); v.addWidget(b)
        f = QFormLayout()
        self.p = QComboBox(); self.p.addItems(self.PARAMS)
        self.val = QLineEdit(); self.val.setPlaceholderText("число | строка | 4 числа через запятую (для цвета)")
        f.addRow("Параметр", self.p); f.addRow("Значение", self.val)
        v.addLayout(f)
        self.log = QPlainTextEdit(); self.log.setReadOnly(True); self.log.setMaximumHeight(100)
        v.addWidget(self.log)
        go = QPushButton("Применить ко всем (создаются .bak)"); go.clicked.connect(self.apply); v.addWidget(go)

    def add(self):
        fs, _ = QFileDialog.getOpenFileNames(self, "RVMAT", "", "RVMAT (*.rvmat)")
        self.files.addItems(fs)

    def apply(self):
        name, raw = self.p.currentText(), self.val.text().strip()
        n = 0
        for i in range(self.files.count()):
            path = self.files.item(i).text()
            try:
                m = Material.load(path)
                if name in ("PixelShaderID", "VertexShaderID"):
                    m.set_string(name, raw)
                elif name == "specularPower":
                    m.set_scalar(name, float(raw))
                else:
                    vals = [float(x) for x in raw.split(",")]
                    m.set_color(name, vals)
                m.save(path, backup=True)
                n += 1
                self.log.appendPlainText(f"OK  {path}")
            except Exception as e:
                self.log.appendPlainText(f"ERR {path}: {e}")
        self.log.appendPlainText(f"Готово: {n}/{self.files.count()}")
