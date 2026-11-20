"""Левая панель: текстуры, пресеты, состояния."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QFileDialog, QHBoxLayout, QInputDialog, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QMenu, QMessageBox, QPlainTextEdit,
                               QPushButton, QTabWidget, QVBoxLayout, QWidget)

from ..backend import ALL_STATES, State, diff_materials, kit_filename
from ..core.presets import CATEGORIES, PresetManager
from ..core.project import Project
from ..core.textures import (ALL_EXT, STAGE_ROLE, ThumbCache, auto_match, guess_stage, probe, scan_folder)
from .widgets import pixmap_from_rgba
from PySide6.QtGui import QPixmap
import numpy as np


class TexturesTab(QWidget):
    assign = Signal(int, str)           # stage, физический путь
    open_viewer = Signal(str)
    library_changed = Signal()

    def __init__(self, proj: Project, thumbs: ThumbCache):
        super().__init__()
        self.proj, self.thumbs = proj, thumbs
        v = QVBoxLayout(self)
        row = QHBoxLayout()
        b1, b2, b3 = QPushButton("+ Файлы"), QPushButton("+ Папка"), QPushButton("Авто-назначение")
        b1.clicked.connect(self.add_files); b2.clicked.connect(self.add_folder); b3.clicked.connect(self.auto_assign)
        b3.setToolTip("Сопоставить по общему имени: _nohq/_as/_smdi → стадии RVMAT, _co → Base Color (превью)")
        for b in (b1, b2, b3):
            row.addWidget(b)
        v.addLayout(row)
        self.search = QLineEdit(); self.search.setPlaceholderText("Поиск текстур…")
        self.search.textChanged.connect(self.reload)
        v.addWidget(self.search)
        self.list = QListWidget()
        self.list.setViewMode(QListWidget.IconMode)
        self.list.setIconSize(QSize(72, 72))
        self.list.setGridSize(QSize(100, 112))
        self.list.setResizeMode(QListWidget.Adjust)
        self.list.setMovement(QListWidget.Static)
        self.list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._menu)
        self.list.itemDoubleClicked.connect(lambda it: self.open_viewer.emit(it.data(Qt.UserRole)))
        self.list.currentItemChanged.connect(self._info)
        v.addWidget(self.list, 1)
        self.info = QLabel("")
        self.info.setObjectName("hint"); self.info.setWordWrap(True)
        v.addWidget(self.info)
        self.reload()

    def add_paths(self, paths):
        for p in paths:
            s = str(p)
            if s not in self.proj.textures:
                self.proj.textures.append(s)
        self.proj.dirty = True
        self.reload()
        self.library_changed.emit()

    def add_files(self):
        fs, _ = QFileDialog.getOpenFileNames(self, "Текстуры", "", "Текстуры (" + " ".join("*" + e for e in sorted(ALL_EXT)) + ")")
        if fs:
            self.add_paths(fs)

    def add_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Папка с текстурами")
        if d:
            self.add_paths(scan_folder(d))

    def reload(self):
        t = self.search.text().lower()
        self.list.clear()
        for s in self.proj.textures:
            p = Path(s)
            if t and t not in p.name.lower():
                continue
            it = QListWidgetItem(p.name)
            it.setData(Qt.UserRole, s)
            st = guess_stage(p)
            it.setToolTip(f"{s}\n" + ("Base Color (_co): не стадия RVMAT" if st == 0 else f"Стадия по имени: {st or '—'}"))
            arr = self.thumbs.get(p)
            if arr is not None:
                rgba = np.dstack([arr[..., :3], np.full(arr.shape[:2], 255, np.uint8)])
                it.setIcon(QIcon(pixmap_from_rgba(rgba, 72)))
            self.list.addItem(it)

    def _info(self, it):
        if not it:
            self.info.setText("")
            return
        ti = probe(it.data(Qt.UserRole))
        probs = ti.problems()
        self.info.setText(f"{ti.name}\n{ti.width}×{ti.height} · {ti.fmt}{' · α' if ti.has_alpha else ''}"
                          + ("\n⚠ " + "; ".join(probs) if probs else ""))

    def _menu(self, pos):
        it = self.list.itemAt(pos)
        if not it:
            return
        path = it.data(Qt.UserRole)
        m = QMenu(self)
        m.addAction("Назначить как Base Color (_co) — только превью", lambda: self.assign.emit(0, path))
        m.addSeparator()
        for i in range(1, 8):
            m.addAction(f"Назначить на Stage{i} — {STAGE_ROLE[i]}", lambda i=i: self.assign.emit(i, path))
        m.addSeparator()
        m.addAction("Открыть просмотр…", lambda: self.open_viewer.emit(path))
        m.addAction("Убрать из библиотеки", lambda: self._remove([i.data(Qt.UserRole) for i in self.list.selectedItems()]))
        m.exec(self.list.mapToGlobal(pos))

    def _remove(self, paths):
        self.proj.textures = [t for t in self.proj.textures if t not in paths]
        self.reload()

    def auto_assign(self):
        groups = auto_match([Path(t) for t in self.proj.textures])
        if not groups:
            QMessageBox.information(self, "Авто-назначение", "Не найдено файлов с суффиксами _nohq/_as/_smdi/_co.")
            return
        names = sorted(groups)
        name, ok = QInputDialog.getItem(self, "Авто-назначение", "Набор текстур (по общему имени):", names, 0, False)
        if ok:
            for st, p in groups[name].items():
                self.assign.emit(st, str(p))


class PresetsTab(QWidget):
    apply = Signal(str)
    save_current = Signal()

    def __init__(self, pm: PresetManager):
        super().__init__()
        self.pm = pm
        v = QVBoxLayout(self)
        r = QHBoxLayout()
        self.search = QLineEdit(); self.search.setPlaceholderText("Поиск пресетов…")
        self.cat = QComboBox(); self.cat.addItem("Все категории", ""); [self.cat.addItem(c, c) for c in CATEGORIES]
        self.fav = QPushButton("★"); self.fav.setCheckable(True); self.fav.setFixedWidth(30); self.fav.setToolTip("Только избранное")
        r.addWidget(self.search, 1); r.addWidget(self.cat); r.addWidget(self.fav)
        v.addLayout(r)
        self.list = QListWidget()
        v.addWidget(self.list, 1)
        self.desc = QLabel(""); self.desc.setObjectName("hint"); self.desc.setWordWrap(True)
        v.addWidget(self.desc)
        g = QHBoxLayout()
        for text, fn in (("Применить", self._apply), ("★/☆", self._toggle_fav), ("Копия", self._dup), ("Удалить", self._del)):
            b = QPushButton(text); b.clicked.connect(fn); g.addWidget(b)
        v.addLayout(g)
        g2 = QHBoxLayout()
        for text, fn in (("Сохранить текущий…", self.save_current.emit), ("Импорт", self._import), ("Экспорт", self._export)):
            b = QPushButton(text); b.clicked.connect(fn); g2.addWidget(b)
        v.addLayout(g2)
        self.search.textChanged.connect(self.reload); self.cat.currentIndexChanged.connect(self.reload); self.fav.toggled.connect(self.reload)
        self.list.currentItemChanged.connect(lambda it: self.desc.setText(
            (f"{self.pm.items[it.data(Qt.UserRole)].category} — {self.pm.items[it.data(Qt.UserRole)].description}") if it else ""))
        self.list.itemDoubleClicked.connect(lambda it: self.apply.emit(it.data(Qt.UserRole)))
        self.reload()

    def current_id(self):
        it = self.list.currentItem()
        return it.data(Qt.UserRole) if it else None

    def reload(self):
        self.list.clear()
        for p in self.pm.search(self.search.text(), self.cat.currentData() or "", self.fav.isChecked()):
            it = QListWidgetItem(("★ " if p.favorite else "") + p.name + ("" if p.builtin else "  (мой)"))
            it.setData(Qt.UserRole, p.id)
            it.setToolTip(p.description)
            self.list.addItem(it)

    def _apply(self):
        if self.current_id():
            self.apply.emit(self.current_id())

    def _toggle_fav(self):
        i = self.current_id()
        if i:
            self.pm.set_favorite(i, not self.pm.items[i].favorite)
            self.reload()

    def _dup(self):
        i = self.current_id()
        if i:
            self.pm.duplicate(i); self.reload()

    def _del(self):
        i = self.current_id()
        if not i:
            return
        try:
            if QMessageBox.question(self, "Удалить", "Удалить пресет?") == QMessageBox.Yes:
                self.pm.delete(i); self.reload()
        except PermissionError as e:
            QMessageBox.information(self, "Пресеты", str(e))

    def _import(self):
        f, _ = QFileDialog.getOpenFileName(self, "Импорт пресетов", "", "Пресеты (*.json *.rvpresets)")
        if f:
            try:
                n = len(self.pm.import_from(f))
                self.reload()
                QMessageBox.information(self, "Импорт", f"Импортировано: {n}")
            except Exception as e:
                QMessageBox.warning(self, "Импорт", f"Ошибка: {e}")

    def _export(self):
        i = self.current_id()
        if not i:
            return
        f, _ = QFileDialog.getSaveFileName(self, "Экспорт пресета", f"{self.pm.items[i].name}.json", "Пресеты (*.json)")
        if f:
            self.pm.export_to([i], f)


class StatesTab(QWidget):
    selected = Signal(str)
    toggled = Signal(str, bool)

    def __init__(self, proj: Project):
        super().__init__()
        self.proj = proj
        v = QVBoxLayout(self)
        v.addWidget(QLabel("Состояния комплекта (галочка — создавать файл):"))
        self.list = QListWidget()
        v.addWidget(self.list)
        self.diff = QPlainTextEdit(); self.diff.setReadOnly(True)
        v.addWidget(QLabel("Отличия выбранного от базового:"))
        v.addWidget(self.diff, 1)
        self.list.currentItemChanged.connect(self._sel)
        self.list.itemChanged.connect(lambda it: self.toggled.emit(it.data(Qt.UserRole), it.checkState() == Qt.Checked))
        self.refresh()

    def refresh(self):
        cur = self.current_state()
        self.list.blockSignals(True)
        self.list.clear()
        for s in ALL_STATES:
            it = QListWidgetItem(kit_filename(self.proj.name, s) + ("" if s.confirmed else "   (base: Stage3 не подтверждён)"))
            it.setData(Qt.UserRole, s.value)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if s in self.proj.enabled else Qt.Unchecked)
            self.list.addItem(it)
            if s.value == cur:
                self.list.setCurrentItem(it)
        self.list.blockSignals(False)
        self._sel(self.list.currentItem())

    def current_state(self):
        it = self.list.currentItem()
        return it.data(Qt.UserRole) if it else None

    def _sel(self, it):
        if not it:
            self.diff.setPlainText("")
            return
        s = State(it.data(Qt.UserRole))
        base = self.proj.effective(State.BASE)
        d = diff_materials(base, self.proj.effective(s))
        self.diff.setPlainText("\n".join(f"{k}: {a} → {b}" for k, a, b in d) or "(нет отличий)")
        self.selected.emit(s.value)
