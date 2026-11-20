DARK = """
* { font-family: "Segoe UI", "Noto Sans", sans-serif; font-size: 12px; }
QMainWindow, QDialog, QWidget { background: #1e1f24; color: #d9dbe0; }
QDockWidget { titlebar-close-icon: none; font-weight: 600; }
QDockWidget::title { background: #272930; padding: 5px 8px; border-bottom: 1px solid #34363f; }
QMenuBar, QMenu, QToolBar { background: #23252b; border: none; }
QMenu::item:selected, QMenuBar::item:selected { background: #3b6fd8; }
QToolBar { spacing: 4px; padding: 3px; border-bottom: 1px solid #34363f; }
QToolButton { padding: 4px 8px; border-radius: 4px; }
QToolButton:hover { background: #343741; }
QPushButton { background: #323540; border: 1px solid #424652; border-radius: 4px; padding: 4px 10px; }
QPushButton:hover { background: #3d4150; }
QPushButton:pressed { background: #3b6fd8; }
QPushButton:disabled { color: #777; }
QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox, QListWidget, QTreeWidget, QTableWidget {
    background: #15161a; border: 1px solid #373a45; border-radius: 3px; padding: 2px 4px; selection-background-color: #3b6fd8; }
QComboBox QAbstractItemView { background: #15161a; selection-background-color: #3b6fd8; }
QTabWidget::pane { border: 1px solid #34363f; top: -1px; }
QTabBar::tab { background: #272930; padding: 5px 10px; border: 1px solid #34363f; border-bottom: none; }
QTabBar::tab:selected { background: #1e1f24; color: #ffffff; border-top: 2px solid #3b6fd8; }
QGroupBox { border: 1px solid #34363f; border-radius: 5px; margin-top: 10px; padding-top: 8px; font-weight: 600; }
QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 4px; color: #9fb4e8; }
QSlider::groove:horizontal { height: 4px; background: #373a45; border-radius: 2px; }
QSlider::handle:horizontal { background: #6d9bff; width: 12px; margin: -5px 0; border-radius: 6px; }
QSlider::sub-page:horizontal { background: #3b6fd8; border-radius: 2px; }
QHeaderView::section { background: #272930; border: 1px solid #34363f; padding: 3px; }
QScrollBar:vertical { background: #1a1b1f; width: 10px; }
QScrollBar::handle:vertical { background: #3d404b; border-radius: 4px; min-height: 24px; }
QScrollBar:horizontal { background: #1a1b1f; height: 10px; }
QScrollBar::handle:horizontal { background: #3d404b; border-radius: 4px; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QStatusBar { background: #23252b; }
QLabel[overridden="true"] { color: #ffb454; font-weight: 600; }
QLabel#hint { color: #8b90a0; }
QSplitter::handle { background: #2a2c34; }
"""
