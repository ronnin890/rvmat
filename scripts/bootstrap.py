"""Подготовка окружения и запуск программы. Вызывается из start.bat интерпретатором из .venv.

Показывает, что происходит (установка видна), проверяет tkinter и ловит падение при старте:
если окно не открылось, ошибка печатается в консоли и сохраняется в файл."""
import importlib.util
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = Path(tempfile.gettempdir()) / "rvmat_studio_start.log"


def say(msg: str = "") -> None:
    print(msg, flush=True)


def has(mod: str) -> bool:
    return importlib.util.find_spec(mod) is not None


def pip(*args: str) -> bool:
    cmd = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--timeout", "30", "--retries", "3", *args]
    return subprocess.run(cmd, cwd=ROOT).returncode == 0


def ensure_deps() -> bool:
    if has("numpy") and has("PIL"):
        return True
    say("Первый запуск: устанавливаю numpy и Pillow (около 40 МБ; обычно 1-2 минуты, зависит от интернета).")
    say("Идёт загрузка - окно программы появится после установки. Не закрывайте это окно.")
    say()
    if not pip("--require-hashes", "-r", "requirements.lock"):
        say()
        say("Lock-файл не подошёл этой версии Python - ставлю по requirements.txt (без проверки хешей).")
        if not pip("-r", "requirements.txt"):
            return False
        if not pip("-r", "requirements-gl.txt"):
            say("moderngl не установился: предпросмотр будет работать на процессоре (медленнее на тяжёлых моделях).")
    return has("numpy") and has("PIL")


def launch() -> int:
    gui = Path(sys.executable).with_name("pythonw.exe")
    exe = str(gui if gui.exists() else sys.executable)
    say("Запускаю DayZ RVMAT Studio...")
    with open(LOG, "wb") as log:
        proc = subprocess.Popen([exe, "-m", "rvmat_studio"], cwd=ROOT, stdout=log, stderr=log)
        try:
            code = proc.wait(timeout=6)            # окно живёт дольше 6 секунд - всё в порядке, консоль можно закрыть
        except subprocess.TimeoutExpired:
            say("Программа запущена.")
            time.sleep(1)
            return 0
    text = LOG.read_text("utf-8", "replace").strip()
    say()
    say(f"Программа завершилась сразу после запуска (код {code}). Причина:")
    say(text or "(сообщений нет; подробности: %APPDATA%\\DayZRvmatStudio\\logs\\startup_error.log)")
    say()
    say(f"Этот текст сохранён в {LOG}. Пришлите его автору.")
    return 1


def main() -> int:
    say(f"Python {sys.version.split()[0]}, папка программы: {ROOT}")
    if not ensure_deps():
        say("Не удалось установить зависимости. Проверьте интернет/прокси и запустите start.bat ещё раз.")
        return 1
    if not has("tkinter"):
        say("В этой установке Python нет tkinter. Переустановите Python с python.org (компонент tcl/tk and IDLE).")
        return 1
    return launch()


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")   # type: ignore[attr-defined]
    except (AttributeError, OSError):
        pass
    sys.exit(main())
