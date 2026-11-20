"""Точка входа GUI (для PyInstaller и `python run_studio.py`)."""
import sys

from rvmat_studio.gui.app import main

if __name__ == "__main__":
    sys.exit(main())
