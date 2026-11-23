"""Гигиена репозитория: CI, зависимости, секреты, документы."""
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WF = sorted((ROOT / ".github" / "workflows").glob("*.yml"))
SECRET_PATTERNS = [r"ghp_[A-Za-z0-9]{30,}", r"github_pat_[A-Za-z0-9_]{40,}", r"AKIA[0-9A-Z]{16}", r"-----BEGIN (RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----",
                   r"xox[baprs]-[A-Za-z0-9-]{10,}", r"AIza[0-9A-Za-z_\-]{35}", r"sk-[A-Za-z0-9]{32,}"]


def tracked_files():
    try:
        out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, timeout=30, check=True).stdout.split("\n")
        return [ROOT / f for f in out if f]
    except (OSError, subprocess.SubprocessError):
        return [p for p in ROOT.rglob("*") if p.is_file() and ".git" not in p.parts]


def test_no_secrets_in_repository():
    hits = []
    for f in tracked_files():
        if not f.is_file() or f.stat().st_size > 2_000_000 or f.suffix.lower() in (".png", ".paa", ".fbx", ".jpg", ".lock"):
            continue
        text = f.read_text("utf-8", "ignore")
        hits += [f"{f.relative_to(ROOT)}: {p}" for p in SECRET_PATTERNS if re.search(p, text)]
    assert not hits, hits


def test_workflows_have_explicit_permissions_and_concurrency():
    assert WF
    for f in WF:
        t = f.read_text("utf-8")
        assert re.search(r"^permissions:\s*$", t, re.M), f"{f.name}: нет permissions верхнего уровня"
        assert re.search(r"^\s+contents:\s*read", t, re.M), f"{f.name}: contents: read"
        if f.name != "release.yml":
            assert "concurrency:" in t, f"{f.name}: нет concurrency"


def test_actions_are_pinned_or_marked_for_pinning():
    """Каждое `uses:` — SHA коммита (40 hex) либо тег с пометкой `pin: scripts/pin_actions.py` (запуск скрипта закрепляет SHA)."""
    bad, unpinned = [], 0
    for f in WF:
        for line in f.read_text("utf-8").split("\n"):
            m = re.search(r"uses:\s*([\w./-]+)@(\S+)", line)
            if not m:
                continue
            if re.fullmatch(r"[0-9a-f]{40}", m.group(2)):
                continue
            unpinned += 1
            if "scripts/pin_actions.py" not in line:
                bad.append(line.strip())
    assert not bad, bad
    print(f"незакреплённых действий: {unpinned}")


def test_pull_request_trigger_dependabot_and_codeql_present():
    assert re.search(r"^\s*pull_request:", (ROOT / ".github/workflows/build.yml").read_text("utf-8"), re.M)
    d = (ROOT / ".github/dependabot.yml").read_text("utf-8")
    assert "package-ecosystem: pip" in d and "package-ecosystem: github-actions" in d and "weekly" in d
    assert "codeql-action" in (ROOT / ".github/workflows/codeql.yml").read_text("utf-8")


def test_lock_file_has_hashes_and_matches_requirements():
    lock = (ROOT / "requirements.lock").read_text("utf-8")
    assert lock.count("--hash=sha256:") >= 4
    for name in ("numpy", "pillow", "moderngl"):
        assert re.search(rf"^{name}==", lock, re.M | re.I), name
    assert "--require-hashes" in (ROOT / "start.bat").read_text("utf-8")


def test_dependency_upper_bounds():
    t = (ROOT / "pyproject.toml").read_text("utf-8")
    assert re.search(r"numpy>=[\d.]+,<\d", t) and re.search(r"Pillow>=[\d.]+,<\d", t) and re.search(r"moderngl>=[\d.]+,<\d", t)


def test_docs_and_gitignore():
    assert (ROOT / "SECURITY.md").read_text("utf-8").strip()
    assert "## Безопасность" in (ROOT / "README.md").read_text("utf-8")
    gi = (ROOT / ".gitignore").read_text("utf-8").split()
    for entry in ("settings.json", "recovery/", "presets/", "logs/"):
        assert entry in gi


def test_start_bat_guards_cd_before_rmdir():
    t = (ROOT / "start.bat").read_bytes().decode("utf-8")
    assert t.index('cd /d "%~dp0" ||') < t.index("rmdir /s /q")
