from __future__ import annotations

from pathlib import Path

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout,
                               QLabel, QLineEdit, QListWidget, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea,
                               QSpinBox, QVBoxLayout, QWidget)

from ..backend import Material
from ..core.export import ExportPlan, execute
from ..core.settings import Settings
from ..core.textures import channel_view, load_rgba, normal_report, probe
from .widgets import pixmap_from_rgba


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
        self.s.renderer = self.renderer.currentData()
        super().accept()


class ExportDialog(QDialog):
    """Показывает список файлов, проблемы и предупреждение о перезаписи."""
    def __init__(self, plan: ExportPlan, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Проверка перед экспортом")
        self.resize(720, 460)
        self.plan = plan
        v = QVBoxLayout(self)
        v.addWidget(QLabel("<b>Будут записаны файлы:</b>"))
        lst = QListWidget()
        for p in plan.files:
            lst.addItem(("⚠ перезапись: " if p in plan.existing else "") + str(p))
        v.addWidget(lst, 1)
        v.addWidget(QLabel(f"<b>Диагностика</b> ({len(plan.issues)}):"))
        t = QPlainTextEdit(); t.setReadOnly(True)
        t.setPlainText("\n".join(str(i) for i in plan.issues) or "Замечаний нет")
        v.addWidget(t, 1)
        self.over = QCheckBox("Разрешить перезапись существующих файлов (будут созданы .bak)")
        self.over.setVisible(bool(plan.existing))
        v.addWidget(self.over)
        if plan.has_errors:
            w = QLabel("⛔ Есть критические ошибки. Экспорт возможен, но файл может не работать в игре.")
            w.setStyleSheet("color:#ff7b72"); v.addWidget(w)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Экспортировать")
        bb.accepted.connect(self._ok); bb.rejected.connect(self.reject)
        v.addWidget(bb)

    def _ok(self):
        if self.plan.existing and not self.over.isChecked():
            QMessageBox.warning(self, "Экспорт", "Файлы уже существуют. Отметьте перезапись или выберите другую папку.")
            return
        self.accept()


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
