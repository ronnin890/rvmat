"""python -m rvmat_studio            — запуск окна программы
python -m rvmat_studio kit base.rvmat name outdir | validate file.rvmat   — консольные команды"""
import sys
from pathlib import Path


def cli(a) -> int:
    from .backend import KitSpec, Material, validate, validate_kit
    if len(a) >= 2 and a[0] == "validate":
        issues = validate(Material.load(a[1]))
        for i in issues:
            print(i)
        return 1 if any(i.severity.name == "ERROR" for i in issues) else 0
    if len(a) == 4 and a[0] == "kit":
        kit = KitSpec(a[2], Material.load(a[1])).render_all()
        for fn, m in kit.items():
            m.save(Path(a[3]) / fn)
            print("written", Path(a[3]) / fn)
        for i in validate_kit(kit, a[2]):
            print(i)
        return 0
    print(__doc__)
    return 2


def _crash() -> int:
    """Ошибка при запуске (до появления окна): в консоль, в файл журнала и в окно сообщения."""
    import traceback
    text = traceback.format_exc()
    print(text, file=sys.stderr)
    where = ""
    try:
        from .core.settings import app_dir
        d = app_dir() / "logs"
        d.mkdir(parents=True, exist_ok=True)
        (d / "startup_error.log").write_text(text, "utf-8")
        where = f"\n\nПодробности: {d / 'startup_error.log'}"
    except Exception:
        pass
    try:
        import tkinter
        from tkinter import messagebox
        r = tkinter.Tk(); r.withdraw()
        messagebox.showerror("DayZ RVMAT Studio", "Не удалось запустить программу:\n\n" + text.strip().splitlines()[-1] + where)
        r.destroy()
    except Exception:
        pass
    return 1


def main():
    try:
        return _main()
    except SystemExit:
        raise
    except BaseException:
        return _crash()


def _main():
    a = sys.argv[1:]
    if a and a[0] in ("kit", "validate"):
        return cli(a)
    if a and a[0] == "--selftest":
        from .ui.selftest import selftest
        return selftest()
    from .ui.app import main as gui
    return gui(a)


if __name__ == "__main__":
    raise SystemExit(main())
