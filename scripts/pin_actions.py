"""Закрепляет `uses: owner/repo@tag` в .github/workflows по SHA коммита (нужен `gh` с доступом к GitHub).

Запуск:  python scripts/pin_actions.py     (результат: uses: owner/repo@<sha> # tag)
Dependabot (github-actions) затем сам обновляет SHA вместе с комментарием о версии."""
import json
import re
import subprocess
import sys
from pathlib import Path

PAT = re.compile(r"(uses:\s*)([\w.-]+/[\w./-]+)@([\w.-]+)(\s*#.*)?$")


def sha_for(repo: str, tag: str) -> str:
    base = "/".join(repo.split("/")[:2])
    out = subprocess.run(["gh", "api", f"repos/{base}/commits/{tag}"], capture_output=True, text=True, timeout=60)
    if out.returncode:
        raise SystemExit(f"{repo}@{tag}: {out.stderr.strip()}")
    return json.loads(out.stdout)["sha"]


def main() -> int:
    for f in sorted(Path(".github/workflows").glob("*.yml")):
        lines = f.read_text("utf-8").split("\n")
        for i, line in enumerate(lines):
            m = PAT.search(line)
            if m and not re.fullmatch(r"[0-9a-f]{40}", m.group(3)):
                lines[i] = f"{line[:m.start()]}{m.group(1)}{m.group(2)}@{sha_for(m.group(2), m.group(3))} # {m.group(3)}"
        f.write_text("\n".join(lines), "utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
