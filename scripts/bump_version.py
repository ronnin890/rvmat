"""Меняет версию везде одной командой:  python scripts/bump_version.py 0.4.0
Файлы: rvmat_studio/__init__.py, pyproject.toml, packaging/installer.iss. Запись в CHANGELOG.md добавляйте вручную."""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RULES = [("rvmat_studio/__init__.py", r'(__version__ = ")[^"]+(")'), ("pyproject.toml", r'(?m)^(version = ")[^"]+(")'),
         ("packaging/installer.iss", r'(#define AppVersion ")[^"]+(")')]


def main(argv) -> int:
    if len(argv) != 2 or not re.fullmatch(r"\d+\.\d+\.\d+", argv[1]):
        print(__doc__)
        return 2
    for rel, pat in RULES:
        p = ROOT / rel
        new, n = re.subn(pat, lambda m: m.group(1) + argv[1] + m.group(2), p.read_text("utf-8"), count=1)
        if n != 1:
            print(f"{rel}: версия не найдена")
            return 1
        p.write_text(new, "utf-8")
        print(f"{rel}: {argv[1]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
