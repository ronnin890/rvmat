"""Пути, внешние программы, схемы, атомарная запись, журнал."""
import json
import os
import re
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from rvmat_studio.core import autoload, dayztools, export as ex, limits, logs, safeio, texnames
from rvmat_studio.core.presets import PRESET_ID, Preset, PresetManager, super_template
from rvmat_studio.core.project import Project
from rvmat_studio.core.settings import Settings, app_dir

ROOT = Path(__file__).resolve().parent.parent
POSIX = sys.platform != "win32"


def png(path, color=(128, 128, 255, 255)):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", (8, 8), color).save(path)
    return path


# ---------------------------------------------------------------- ensure_within / экспорт
def test_ensure_within_basic(tmp_path):
    (tmp_path / "a").mkdir()
    assert safeio.ensure_within(tmp_path / "a" / "x.txt", tmp_path / "a")
    for bad in (tmp_path / "a" / ".." / "x", tmp_path / "b", "/etc/passwd", tmp_path / "a" / ".." / ".." / "y"):
        with pytest.raises(safeio.PathError):
            safeio.ensure_within(bad, tmp_path / "a")
    with pytest.raises(safeio.PathError):
        safeio.ensure_within("a\x00b", tmp_path)


@pytest.mark.skipif(not POSIX, reason="symlink")
def test_ensure_within_rejects_symlink_escape(tmp_path):
    out = tmp_path / "out"; out.mkdir()
    (tmp_path / "secret").mkdir()
    (out / "link").symlink_to(tmp_path / "secret")
    with pytest.raises(safeio.PathError):
        safeio.ensure_within(out / "link" / "f.rvmat", out)


def test_run_op_refuses_paths_outside_export_dir(tmp_path):
    out = tmp_path / "out"; out.mkdir()
    for dst in (tmp_path / "evil.rvmat", out / ".." / "evil.rvmat", Path("/tmp/evil_rvmat.rvmat")):
        with pytest.raises(ex.ExportError):
            ex.run_op(ex.Op("rvmat", str(dst), text="x"), True, root=str(out))
    assert not (tmp_path / "evil.rvmat").exists()
    ex.run_op(ex.Op("rvmat", str(out / "data" / "ok.rvmat"), text="x"), True, root=str(out))
    assert (out / "data" / "ok.rvmat").read_text() == "x"


def test_run_export_collects_error_for_outside_op(tmp_path):
    out = tmp_path / "out"
    plan = ex.ExportPlan(str(out), str(out / "data"), "m", ops=[ex.Op("rvmat", str(tmp_path / "x.rvmat"), text="x"), ex.Op("rvmat", str(out / "data" / "a.rvmat"), text="y")])
    res = ex.run_export(plan, True)
    assert len(res.errors) == 1 and "вне папки экспорта" in res.errors[0] and len(res.written) == 1
    assert not (tmp_path / "x.rvmat").exists()


def test_plan_export_names_cannot_escape(tmp_path):
    p = Project("..\\..\\evil/../x")
    p.slots[0].label = "../../up"
    p.mesh_name = "../../outside"
    plan = ex.plan_export(p, {"out_root": str(tmp_path / "out"), "mesh_name": "../../outside", "textures": False, "fbx": False})
    for o in plan.ops:
        assert safeio.is_within(o.dst, tmp_path / "out")
    assert plan.mesh_name and "/" not in plan.mesh_name and ".." not in plan.mesh_name


# ---------------------------------------------------------------- поиск текстур
def test_find_texture_rejects_traversal_unc_and_absolute(tmp_path):
    root = tmp_path / "P"; png(root / "mod" / "a.png")
    outside = png(tmp_path / "secret" / "s.png")
    warns = []
    f = lambda t, **k: texnames.find_texture(t, str(root), None, (), allowed=[str(root)], warn=warns.append, **k)
    assert f("mod\\a.paa").name == "a.png"                                 # исходник вместо .paa — допустим
    assert f("..\\secret\\s.paa") is None
    assert f("mod\\..\\..\\secret\\s.png") is None
    assert f("\\\\server\\share\\x.paa") is None
    assert f("//?/C:/x.paa") is None
    assert f(str(outside)) is None                                         # абсолютный путь вне корней
    assert len(warns) >= 4
    ok = texnames.find_texture(str(root / "mod" / "a.png"), str(root), None, (), allowed=[str(root)])
    assert ok is not None                                                  # абсолютный путь внутри корня разрешён


def test_find_texture_mapping_is_explicit_choice(tmp_path):
    f = png(tmp_path / "elsewhere" / "x.png")
    assert texnames.find_texture("x.paa", "", {"x.paa": str(f)}, (), allowed=[]) == f


@pytest.mark.skipif(not POSIX, reason="symlink")
def test_find_texture_symlink_out_of_root(tmp_path):
    root = tmp_path / "P"; root.mkdir()
    out = png(tmp_path / "secret" / "s.png")
    (root / "s.png").symlink_to(out)
    warns = []
    assert texnames.find_texture("s.png", str(root), None, (), allowed=[str(root)], warn=warns.append) is None
    assert warns


def test_project_find_texture_uses_sandbox(tmp_path):
    root = tmp_path / "P"; png(root / "m" / "t.png")
    p = Project("x"); p.project_root = str(root)
    assert p.find_texture("m\\t.paa") is not None
    assert p.find_texture("..\\..\\etc\\passwd") is None and p.path_warnings


# ---------------------------------------------------------------- внешние ссылки проекта
def test_external_refs_listed_and_droppable(tmp_path):
    proj_dir = tmp_path / "work"; proj_dir.mkdir()
    own = png(proj_dir / "tex" / "own.png")
    p = Project("x"); p.path = proj_dir / "x.rvproj"
    p.project_root = "C:\\Windows" if not POSIX else "/etc"
    p.export_dir = str(tmp_path / "elsewhere")
    p.model_path = "\\\\srv\\share\\m.fbx"
    p.textures = [str(own), "/abs/outside/t.png", "..\\..\\t.png"]
    p.tex_map = {"a.paa": "/abs/outside/a.png", "b.paa": str(own)}
    p.slots[0].base_color = "/abs/co.png"
    refs = dict((k, v) for k, v in p.external_refs())
    assert "Корень проекта" in refs and "Папка экспорта" in refs and "Модель" in refs
    values = {v for _, v in p.external_refs()}
    assert str(own) not in values and "/abs/outside/t.png" in values and "..\\..\\t.png" in values and "/abs/outside/a.png" in values and "/abs/co.png" in values
    p.drop_refs(p.external_refs())
    assert p.project_root == "" and p.model_path == "" and p.export_dir == ""
    assert p.textures == [str(own)] and p.tex_map == {"b.paa": str(own)} and p.slots[0].base_color == ""
    assert p.external_refs() == []


def test_root_containing_project_folder_is_not_external(tmp_path):
    root = tmp_path / "P"; (root / "Mod").mkdir(parents=True)
    p = Project("x"); p.path = root / "Mod" / "x.rvproj"; p.project_root = str(root)
    p.textures = [str(png(root / "Mod" / "t.png")), str(png(root / "Other" / "t2.png"))]
    assert p.external_refs() == []                       # P:\ содержит папку проекта; файлы внутри корня — свои


def test_saved_project_roundtrip_has_no_external_refs_for_local_files(tmp_path):
    p = Project("x"); p.path = tmp_path / "x.rvproj"
    p.save()
    assert Project.load(tmp_path / "x.rvproj").external_refs() == []


# ---------------------------------------------------------------- схемы
def valid_dict():
    return Project("mat").to_dict()


def test_schema_accepts_valid_and_ignores_unknown_keys():
    d = valid_dict(); d["future_key"] = {"x": 1}; d["export_opts"]["unknown"] = 5
    p = Project.from_dict(d)
    assert p.slots[0].name == "mat" and "unknown" not in p.export_opts


@pytest.mark.parametrize("mutate", [
    lambda d: d.update(slots="oops"),
    lambda d: d.update(slots=[]),
    lambda d: d.update(slots=[{"name": 5, "rvmat": "x"}]),
    lambda d: d["slots"][0].update(rvmat=123),
    lambda d: d["slots"][0].update(rvmat="class {{{"),
    lambda d: d["slots"][0].update(name=""),
    lambda d: d["slots"][0].update(normal_strength="big"),
    lambda d: d["slots"][0].update(normal_strength=1e9),
    lambda d: d["slots"][0].update(overrides={"nonexistent_state": {"a": 1}}),
    lambda d: d["slots"][0].update(overrides={"damage": {"a": {"deep": 1}}}),
    lambda d: d.update(part_map=[1, 2]),
    lambda d: d.update(part_map={"a": 5}),
    lambda d: d.update(tex_map={"a": ["x"]}),
    lambda d: d.update(textures="notalist"),
    lambda d: d.update(textures=[1, 2]),
    lambda d: d.update(model_path={"a": 1}),
    lambda d: d.update(export_opts={"textures": "yes"}),
    lambda d: d.update(title="x" * 100000),
    lambda d: d.update(project_root="a\x00b"),
    lambda d: d.update(slots=[valid_dict()["slots"][0]] * (limits.MAX_SLOTS + 1)),
], ids=lambda f: str(abs(hash(f)) % 1000))
def test_schema_rejects_bad_types(mutate):
    d = valid_dict()
    mutate(d)
    with pytest.raises(safeio.SchemaError):
        Project.from_dict(d)


def test_newer_format_version_warns():
    d = valid_dict(); d["version"] = 99
    p = Project.from_dict(d)
    assert any("новой версией" in w for w in p.load_warnings)


def test_export_opts_cannot_carry_tool_paths():
    d = valid_dict(); d["export_opts"] = {"image_to_paa": "C:\\evil.exe", "workbench": "x.exe", "converter": "tool", "out_root": "o"}
    p = Project.from_dict(d)
    assert "image_to_paa" not in p.export_opts and "workbench" not in p.export_opts and p.export_opts["converter"] == "tool"


def test_v1_project_still_loads():
    d = {"name": "old", "rvmat": super_template().to_text()}
    p = Project.from_dict(d)
    assert p.title == "old" and len(p.slots) == 1


def test_project_save_load_roundtrip_and_backup(tmp_path):
    p = Project("round"); f = tmp_path / "r.rvproj"
    p.save(f); p.set_value("shared", "specularPower", 33); p.save(f)
    assert (tmp_path / "r.rvproj.bak").exists()
    assert Project.load(f).slots[0].spec.shared.get("specularPower") == 33
    assert not [x for x in tmp_path.iterdir() if x.name.endswith(".tmp")]


def test_autosave_name_is_sanitized():
    p = Project("../../../../evil name?*")
    p.dirty = True
    path = p.autosave_path()
    assert path.parent == (app_dir() / "recovery").resolve() and ".." not in path.name and "/" not in path.name
    p.autosave()
    assert path.exists()
    p.clear_autosave()


# ---------------------------------------------------------------- пресеты
def test_preset_id_rules(tmp_path):
    assert PRESET_ID.match("0123456789ab") and not PRESET_ID.match("../../x") and not PRESET_ID.match("ABCDEF123456") and not PRESET_ID.match("short")
    pm = PresetManager(tmp_path)
    with pytest.raises(safeio.PathError):
        pm._path("../../evil")
    with pytest.raises(safeio.PathError):
        pm._path("metal_polished")


def test_preset_files_with_bad_id_are_skipped(tmp_path):
    good = Preset("Ok", "Металл", "", super_template().to_text(), id="0123456789ab")
    (tmp_path / "0123456789ab.rvpreset").write_text(json.dumps(good.__dict__))
    bad = dict(good.__dict__, id="../../../evil")
    (tmp_path / "evil.rvpreset").write_text(json.dumps(bad))
    clash = dict(good.__dict__, id="metal_polished")
    (tmp_path / "metal_polished.rvpreset").write_text(json.dumps(clash))
    mismatch = dict(good.__dict__, id="aaaaaaaaaaaa")
    (tmp_path / "bbbbbbbbbbbb.rvpreset").write_text(json.dumps(mismatch))
    (tmp_path / "junk.rvpreset").write_text("{not json")
    pm = PresetManager(tmp_path)
    ids = set(pm.items)
    assert "0123456789ab" in ids and "../../../evil" not in ids and "aaaaaaaaaaaa" not in ids
    assert pm.items["metal_polished"].builtin                         # встроенный не заменён
    assert len(pm.warnings) >= 4


def test_preset_import_regenerates_id_and_validates(tmp_path):
    pm = PresetManager(tmp_path / "p")
    f = tmp_path / "in.json"
    mk = lambda **k: json.dumps({"rvmat_studio_presets": 1, "items": [dict({"name": "n", "category": "Металл", "description": "", "rvmat": super_template().to_text(), "id": "../../x"}, **k)]})
    f.write_text(mk())
    out = pm.import_from(f)
    assert PRESET_ID.match(out[0].id) and out[0].id != "../../x"
    assert (pm.dir / f"{out[0].id}.rvpreset").exists()
    for bad in (mk(rvmat="class {{"), mk(name=5), mk(name=""), json.dumps({"items": []}), json.dumps({"rvmat_studio_presets": 1, "items": "x"}), "[]"):
        f.write_text(bad)
        with pytest.raises(safeio.DataError):
            pm.import_from(f)
    assert not any(".." in p.name for p in pm.dir.iterdir())


def test_preset_from_dict_ignores_unknown_keys():
    p = Preset.from_dict({"name": "a", "rvmat": super_template().to_text(), "id": "0123456789ab", "zzz": 1, "builtin": True})
    assert p.builtin is False


# ---------------------------------------------------------------- внешние программы
def make_exe(tmp_path, name):
    f = tmp_path / name
    f.write_text("#!/bin/sh\nexit 0\n")
    f.chmod(0o755)
    return f


def test_validate_exe_checks_name_and_type(tmp_path):
    good = make_exe(tmp_path, "ImageToPAA.exe")
    assert dayztools.validate_exe(str(good), "image_to_paa", check_trust=False) == good
    for bad, kind in ((str(make_exe(tmp_path, "calc.exe")), "image_to_paa"), (str(tmp_path), "image_to_paa"), (str(tmp_path / "nope.exe"), "image_to_paa"), ("", "workbench"), (str(good), "workbench")):
        with pytest.raises(dayztools.ToolError):
            dayztools.validate_exe(bad, kind, check_trust=False)
    (tmp_path / "IMAGETOPAA2").mkdir()
    assert dayztools.validate_exe(str(make_exe(tmp_path / "IMAGETOPAA2", "IMAGETOPAA.EXE")), "image_to_paa", False)       # регистр не важен


def test_trust_gate(tmp_path, monkeypatch):
    exe = make_exe(tmp_path, "workbenchApp.exe")
    monkeypatch.setattr(dayztools, "enforce_trust", True)
    monkeypatch.setattr(dayztools, "_trusted", set())
    with pytest.raises(dayztools.ToolError, match="подтверждена"):
        dayztools.validate_exe(str(exe), "workbench")
    dayztools.trust(exe)
    assert dayztools.validate_exe(str(exe), "workbench") == exe
    with pytest.raises(dayztools.WorkbenchError):
        dayztools.check_in_workbench(str(tmp_path / "other.exe"))


def test_convert_with_tool_validates_before_running(tmp_path):
    src = png(tmp_path / "a.png")
    with pytest.raises(dayztools.ToolError):
        texnames.convert_with_tool(src, str(make_exe(tmp_path, "evil.sh")), tmp_path / "a.paa")


def test_settings_roundtrip_and_validation(tmp_path):
    s = Settings(); s.workbench = "wb"; s.trusted_exes = ["a"]
    s.save()
    assert Settings.load().workbench == "wb" and Settings.load().trusted_exes == ["a"]
    Settings.path().write_text(json.dumps({"workbench": 5, "renderer": "evil", "autosave_seconds": "x", "recent": "no", "last_dir": "ok", "unknown": 1, "_warnings": ["x"]}))
    st = Settings.load()
    assert st.workbench == "" and st.renderer == "auto" and st.autosave_seconds == 60 and st.recent == [] and st.last_dir == "ok"
    assert len(st.warnings) >= 3
    Settings.path().write_text("[1,2")
    assert Settings.load().renderer == "auto"
    Settings.path().write_text(json.dumps({"autosave_seconds": 1}))
    assert Settings.load().autosave_seconds == 60 and Settings.load().warnings
    Settings.path().unlink()


def test_open_folder_rejects_missing_and_files(tmp_path):
    assert dayztools.open_folder(tmp_path / "nope") is False
    f = tmp_path / "f.txt"; f.write_text("x")
    assert dayztools.open_folder(f) is False


def test_no_shell_true_and_timeouts():
    """grep-тест: subprocess только списком, без shell=True, run — всегда с timeout; Popen — только Workbench."""
    popen_files, problems = [], []
    for f in (ROOT / "rvmat_studio").rglob("*.py"):
        text = f.read_text("utf-8")
        for m in re.finditer(r"shell\s*=\s*(\w+)", text):
            if m.group(1) != "False":
                problems.append(f"{f.name}: shell={m.group(1)}")
        for m in re.finditer(r"subprocess\.run\((?:[^()]|\([^()]*\))*\)", text, re.S):
            if "timeout" not in m.group(0):
                problems.append(f"{f.name}: run без timeout")
        for pat in (r"os\.system", r"os\.popen", r"(?<![.\w])exec\(", r"(?<![.\w])eval\(", r"pickle\.load", r"yaml\.load\("):
            if re.search(pat, text):
                problems.append(f"{f.name}: {pat}")
        if "subprocess.Popen" in text:
            popen_files.append(f.name)
    assert not problems, problems
    assert popen_files == ["dayztools.py"]


# ---------------------------------------------------------------- атомарная запись
def test_atomic_write_keeps_original_on_failure(tmp_path, monkeypatch):
    f = tmp_path / "a.txt"; f.write_text("old")
    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        safeio.atomic_write(f, "new")
    monkeypatch.undo()
    assert f.read_text() == "old"
    assert not [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]


def test_atomic_write_backup(tmp_path):
    f = tmp_path / "a.rvmat"
    safeio.atomic_write(f, "v1", backup=True)
    assert not (tmp_path / "a.rvmat.bak").exists()
    safeio.atomic_write(f, "v2", backup=True)
    assert f.read_text() == "v2" and (tmp_path / "a.rvmat.bak").read_text() == "v1"


def test_export_rvmat_write_is_atomic_and_backed_up(tmp_path):
    p = Project("m")
    opts = {"out_root": str(tmp_path / "out"), "mesh_name": "m", "textures": False, "fbx": False, "states": ["base"]}
    ex.run_export(ex.plan_export(p, opts))
    res = ex.run_export(ex.plan_export(p, opts), overwrite=True)
    assert not res.errors and (tmp_path / "out" / "m" / "data" / "m.rvmat.bak").exists()
    assert not [x for x in (tmp_path / "out").rglob("*") if x.name.endswith(".tmp")]


# ---------------------------------------------------------------- автозагрузка и ссылки
@pytest.mark.skipif(not POSIX, reason="symlink")
def test_autoload_ignores_symlinks_leaving_the_folder(tmp_path):
    mesh = tmp_path / "m"; (mesh / "data").mkdir(parents=True)
    secret = tmp_path / "secret.rvmat"; secret.write_text(Project("s").slots[0].spec.shared.to_text())
    (mesh / "data" / "m.rvmat").symlink_to(secret)
    (mesh / "m.fbx").symlink_to(png(tmp_path / "x.png"))
    assert autoload.scan_folder(mesh) == []


# ---------------------------------------------------------------- журнал
def test_log_exc_writes_traceback_and_dedupes(monkeypatch):
    logs._recent.clear()
    logs.setup()
    for h in logs.LOGGER.handlers:
        h.flush()
    before = logs.log_path().read_text("utf-8") if logs.log_path().exists() else ""
    for _ in range(3):
        try:
            raise ValueError("boom-xyz")
        except ValueError:
            logs.log_exc("test.where")
    for h in logs.LOGGER.handlers:
        h.flush()
    new = logs.log_path().read_text("utf-8")[len(before):]
    assert new.count("Исключение в test.where") == 1 and "Traceback" in new and "ValueError: boom-xyz" in new


def test_log_rotation_configured():
    import logging.handlers
    logs.setup()
    h = [x for x in logs.LOGGER.handlers if isinstance(x, logging.handlers.RotatingFileHandler)]
    assert h and h[0].maxBytes == logs.MAX_BYTES and h[0].backupCount == logs.BACKUPS
