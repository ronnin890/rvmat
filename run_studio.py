"""Точка входа для PyInstaller и запуска из исходников: python run_studio.py"""
import sys

from rvmat_studio.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
