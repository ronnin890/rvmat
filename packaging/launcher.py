"""Точка входа для PyInstaller (внутри пакета относительные импорты не работают при запуске как скрипта)."""
import sys

from rvmat_studio.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
