"""Экспорт: <корень>/<меш>/<меш>.fbx + data/ (текстуры .paa и .rvmat)."""
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from rvmat_studio.backend import Material, State
from rvmat_studio.core import export as ex
from rvmat_studio.core import fbx, meshes
from rvmat_studio.core.paa import read_paa
from rvmat_studio.core.project import Project
from rvmat_studio.core.texnames import safe_name

OBJ = ("v 0 0 0\nv 2 0 0\nv 2 2 0\nv 0 2 0\nv 4 0 0\nv 4 2 0\nvt 0 0\nvt 1 0\nvt 1 1\nvt 0 1\n"
       "g body\nf 1/1 2/2 3/3 4/4\ng visor\nf 2/1 5/2 6/3 3/4\n")


def png(path, rgba=(120, 80, 200, 255), size=16):
    path.parent.mkdir(parents=True, exist_ok=True)
    a = np.zeros((size, size, 4), np.uint8); a[...] = rgba
    a[:, : size // 2, 0] = 20                                    # неоднородно — чтобы кодер не «схлопнул» блоки
    Image.fromarray(a).save(path)
    return path


@pytest.fixture
def proj(tmp_path):
    src = tmp_path / "src"
    p = Project("helmet")
    p.project_root = str(tmp_path / "P")
    for suf, rgba in (("nohq", (128, 128, 255, 255)), ("smdi", (255, 200, 90, 255)), ("as", (255, 150, 255, 255))):
        png(src / f"h_{suf}.png", rgba)
    png(src / "h_co.png")
    shared = p.spec.shared
    for stage, suf in ((1, "nohq"), (5, "smdi"), (4, "as")):
        p.proj_assign = None
        game = f"src\\h_{suf}.paa"
        p.tex_map[game.lower()] = str(src / f"h_{suf}.png")
        shared.stage(stage).texture = game
    p.set_base_color(str(src / "h_co.png"))
    p.model_path = str(src / "helmet.obj")
    (src / "helmet.obj").write_text(OBJ)
    return p


OPTS = lambda tmp, **kw: {"out_root": str(tmp / "P" / "MyMod"), "p_drive": str(tmp / "P"), "mesh_name": "helmet", "states": ["base", "damage", "destruct"], **kw}


def test_default_prefix_and_names():
    assert ex.default_prefix("P:\\MyMod") == "MyMod" and ex.default_prefix("P:\\") == "" and ex.default_prefix("P:\\a\\b") == "a\\b"
    assert ex.default_prefix("D:\\work\\Mod") == "work\\Mod"                  # вне P: — всё после диска
    assert ex.default_prefix("/home/u/P/MyMod", "/home/u/P") == "MyMod" and ex.default_prefix("/x/y/Mod") == "Mod"
    assert safe_name("Металл полированный") == "Metall_polirovannyy" and safe_name("a b/c") == "a_b_c"


def test_plan_has_requested_layout_and_names(proj, tmp_path):
    plan = ex.plan_export(proj, OPTS(tmp_path))
    out = tmp_path / "P" / "MyMod" / "helmet"
    names = {Path(o.dst).relative_to(out).as_posix() for o in plan.ops}
    assert names == {"helmet.fbx", "data/helmet_nohq.paa", "data/helmet_smdi.paa", "data/helmet_as.paa", "data/helmet_co.paa",
                     "data/helmet.rvmat", "data/helmet_damage.rvmat", "data/helmet_destruct.rvmat"}
    assert not (tmp_path / "P" / "MyMod").exists()                     # план ничего не пишет
    modes = {Path(o.dst).name: o.mode for o in plan.ops if o.kind in ("texture", "fbx")}
    assert modes["helmet_nohq.paa"] == "convert" and modes["helmet.fbx"] == "write"


def test_export_writes_everything_and_rvmat_paths_are_game_paths(proj, tmp_path):
    plan = ex.plan_export(proj, OPTS(tmp_path))
    res = ex.run_export(plan)
    assert not res.errors and len(res.written) == len(plan.ops)
    data = tmp_path / "P" / "MyMod" / "helmet" / "data"
    m = Material.load(data / "helmet_damage.rvmat")
    assert m.stage(1).texture == "MyMod\\helmet\\data\\helmet_nohq.paa"
    assert m.stage(4).texture == "MyMod\\helmet\\data\\helmet_as.paa" and m.stage(5).texture == "MyMod\\helmet\\data\\helmet_smdi.paa"
    assert m.stage(3).texture.endswith("generic_damage_mc.paa")        # состояние damage по-прежнему со своей MC-картой
    assert m.stage(7).texture == "dz\\data\\data\\env_land_co.paa"      # игровые ресурсы не трогаются
    for suf in ("nohq", "smdi", "as", "co"):                           # PAA читаются и совпадают по размеру
        img, kind = read_paa((data / f"helmet_{suf}.paa").read_bytes(), f"helmet_{suf}.paa")
        assert img.shape[:2] == (16, 16) and kind in ("DXT1", "DXT5")
    nohq, _ = read_paa((data / "helmet_nohq.paa").read_bytes(), "helmet_nohq.paa")
    assert abs(int(nohq[4, 12, 0]) - 128) < 12 and abs(int(nohq[4, 12, 1]) - 128) < 12 and nohq[4, 12, 2] > 230   # нормаль после разворота каналов
    fb = fbx.load_fbx(tmp_path / "P" / "MyMod" / "helmet" / "helmet.fbx")
    assert fb.part_names == ["body", "visor"] and len(fb.f) == 4
    assert np.allclose(fb.scale, 2.236, atol=1e-3)                      # исходный размер сохранён при OBJ → FBX


def test_fbx_source_is_copied_not_rewritten(proj, tmp_path):
    src = tmp_path / "src" / "helmet.obj"
    from rvmat_studio.core import fbxwrite
    out = tmp_path / "src" / "orig.fbx"
    fbxwrite.write_fbx(meshes.load_obj(src), out, "orig")
    proj.model_path = str(out)
    plan = ex.plan_export(proj, OPTS(tmp_path))
    fop = next(o for o in plan.ops if o.kind == "fbx")
    assert fop.mode == "copy"
    ex.run_export(plan)
    assert (tmp_path / "P" / "MyMod" / "helmet" / "helmet.fbx").read_bytes() == out.read_bytes()


def test_state_choice_controls_which_rvmats_are_written(proj, tmp_path):
    only_main = ex.plan_export(proj, OPTS(tmp_path, states=["base"]))
    assert [Path(o.dst).name for o in only_main.ops if o.kind == "rvmat"] == ["helmet.rvmat"]
    all_states = ex.plan_export(proj, OPTS(tmp_path, states=[s.value for s in State]))
    assert len([o for o in all_states.ops if o.kind == "rvmat"]) == 6
    names = {Path(o.dst).name for o in all_states.ops if o.kind == "rvmat"}
    assert {"helmet_decorative_damage.rvmat", "helmet_decorative_destruct.rvmat"} <= names


def test_toggle_textures_and_fbx_off(proj, tmp_path):
    plan = ex.plan_export(proj, OPTS(tmp_path, textures=False, fbx=False))
    assert {o.kind for o in plan.ops} == {"rvmat"}
    m = Material.from_text(next(o.text for o in plan.ops if o.dst.endswith("helmet.rvmat")))
    assert m.stage(1).texture == "src\\h_nohq.paa"                     # без экспорта текстур пути остаются прежними


def test_multiple_materials_get_selection_names_and_own_textures(proj, tmp_path):
    src = tmp_path / "src"
    png(src / "g_nohq.png", (128, 128, 255, 255)); png(src / "g_co.png", (10, 200, 10, 255))
    proj.add_slot("glass")
    proj.slot.preset_tag = "glass"
    proj.assign_part("visor", "glass")
    proj.tex_map["src\\g_nohq.paa"] = str(src / "g_nohq.png")
    proj.slot.spec.shared.stage(1).texture = "src\\g_nohq.paa"
    proj.set_base_color(str(src / "g_co.png"))
    plan = ex.plan_export(proj, OPTS(tmp_path, states=["base", "damage"]))
    names = sorted(Path(o.dst).name for o in plan.ops)
    assert "helmet.rvmat" in names and "helmet_damage.rvmat" in names                      # первый материал — по имени меша
    assert "helmet_visor_glass.rvmat" in names and "helmet_visor_glass_damage.rvmat" in names     # селекшен + пресет
    assert "helmet_visor_nohq.paa" in names and "helmet_visor_co.paa" in names and "helmet_nohq.paa" in names
    ex.run_export(plan)
    data = tmp_path / "P" / "MyMod" / "helmet" / "data"
    assert Material.load(data / "helmet_visor_glass.rvmat").stage(1).texture == "MyMod\\helmet\\data\\helmet_visor_nohq.paa"
    assert Material.load(data / "helmet.rvmat").stage(1).texture == "MyMod\\helmet\\data\\helmet_nohq.paa"
    fb = fbx.load_fbx(tmp_path / "P" / "MyMod" / "helmet" / "helmet.fbx")
    assert fb.part_names == ["body", "visor"]


def test_same_source_used_by_two_materials_is_not_duplicated_within_one(proj, tmp_path):
    proj.spec.overrides[State.DAMAGE] = {"Stage1.texture": "src\\h_nohq.paa"}          # то же самое в другом состоянии
    plan = ex.plan_export(proj, OPTS(tmp_path))
    assert len([o for o in plan.ops if o.kind == "texture" and o.role == "nohq"]) == 1


def test_different_file_in_other_state_gets_own_name(proj, tmp_path):
    src = tmp_path / "src"
    png(src / "h_nohq_dmg.png", (128, 128, 255, 255))
    proj.tex_map["src\\h_nohq_dmg.paa"] = str(src / "h_nohq_dmg.png")
    proj.spec.overrides[State.DAMAGE] = {"Stage1.texture": "src\\h_nohq_dmg.paa"}
    plan = ex.plan_export(proj, OPTS(tmp_path))
    assert sorted(Path(o.dst).name for o in plan.ops if o.role == "nohq") == ["helmet_nohq.paa", "helmet_nohq2.paa"]
    ex.run_export(plan)
    data = tmp_path / "P" / "MyMod" / "helmet" / "data"
    assert Material.load(data / "helmet_damage.rvmat").stage(1).texture.endswith("helmet_nohq2.paa")
    assert Material.load(data / "helmet.rvmat").stage(1).texture.endswith("helmet_nohq.paa")


def test_overwrite_is_refused_without_flag_and_backed_up_with_it(proj, tmp_path):
    ex.run_export(ex.plan_export(proj, OPTS(tmp_path)))
    plan = ex.plan_export(proj, OPTS(tmp_path))
    assert len(plan.existing) == len(plan.ops)
    res = ex.run_export(plan)                                          # без overwrite — ничего не затирается
    assert len(res.errors) == len(plan.ops) and not res.written
    res = ex.run_export(plan, overwrite=True)
    assert not res.errors
    data = tmp_path / "P" / "MyMod" / "helmet" / "data"
    assert (data / "helmet.rvmat.bak").exists() and (data / "helmet_nohq.paa.bak").exists()


def test_existing_paa_source_is_copied_verbatim(proj, tmp_path):
    from rvmat_studio.core.paa_write import encode_paa
    a = np.zeros((16, 16, 4), np.uint8); a[...] = (50, 60, 70, 255)
    data, _ = encode_paa(a, "as")
    f = tmp_path / "src" / "ready_as.paa"; f.write_bytes(data)
    proj.tex_map["src\\ready_as.paa"] = str(f)
    proj.spec.shared.stage(4).texture = "src\\ready_as.paa"
    plan = ex.plan_export(proj, OPTS(tmp_path))
    op = next(o for o in plan.ops if o.role == "as")
    assert op.mode == "copy"
    ex.run_export(plan)
    assert (tmp_path / "P" / "MyMod" / "helmet" / "data" / "helmet_as.paa").read_bytes() == data


def test_converter_tool_mode_uses_imagetopaa(proj, tmp_path, monkeypatch):
    calls = []
    def fake(src, tool, out=None):
        calls.append((Path(src).suffix, tool, Path(out).name)); Path(out).write_bytes(b"PAA")
        return out
    monkeypatch.setattr(ex, "convert_with_tool", fake)
    plan = ex.plan_export(proj, OPTS(tmp_path, converter="tool", image_to_paa="X:/ImageToPAA.exe"))
    assert all(o.mode == "convert-tool" for o in plan.ops if o.kind == "texture")
    res = ex.run_export(plan, image_to_paa="X:/ImageToPAA.exe")
    assert not res.errors and len(calls) == 4 and calls[0][1] == "X:/ImageToPAA.exe"
    assert (tmp_path / "P" / "MyMod" / "helmet" / "data" / "helmet_nohq.paa").read_bytes() == b"PAA"


def test_missing_model_and_missing_textures_are_reported_not_fatal(proj, tmp_path):
    proj.model_path = ""
    proj.spec.shared.stage(1).texture = "C:\\nowhere\\x_nohq.paa"
    plan = ex.plan_export(proj, OPTS(tmp_path))
    assert any("Меш не экспортирован" in n for n in plan.notes) and any("не найден" in n for n in plan.notes)
    assert not any(o.kind == "fbx" for o in plan.ops)
    assert not ex.run_export(plan).errors


def test_errors_in_one_file_do_not_stop_the_rest(proj, tmp_path):
    (tmp_path / "src" / "h_nohq.png").write_bytes(b"not an image")
    plan = ex.plan_export(proj, OPTS(tmp_path))
    res = ex.run_export(plan)
    assert len(res.errors) == 1 and "helmet_nohq.paa" in res.errors[0]
    assert (tmp_path / "P" / "MyMod" / "helmet" / "data" / "helmet.rvmat").exists()


def test_plan_requires_a_folder(proj):
    proj.project_root = ""
    with pytest.raises(ValueError):
        ex.plan_export(proj, {"mesh_name": "x"})


def test_validation_issues_are_collected_for_exported_names(proj, tmp_path):
    proj.spec.shared.stage(1).texture = ""
    plan = ex.plan_export(proj, OPTS(tmp_path, textures=False))
    assert any(i.code == "W212" and i.location.startswith("helmet") for i in plan.issues)


# ---------------------------------------------------------------- полный путь с диском (P:\...)
def _png(path, vary=True):
    from PIL import Image
    path.parent.mkdir(parents=True, exist_ok=True)
    a = np.zeros((16, 16, 4), np.uint8); a[...] = (128, 128, 255, 255); a[:, :8, 0] = 20
    Image.fromarray(a).save(path)
    return path


def _drive_project(tmp_path):
    from rvmat_studio.core.project import Project
    p = Project("helmet"); p.project_root = str(tmp_path / "P")
    f = _png(tmp_path / "P" / "src" / "h_as.png")
    p.set_value("shared", "Stage4.texture", "src\\h_as.paa"); p.tex_map["src\\h_as.paa"] = str(f)
    return p


def test_export_writes_full_drive_path_when_enabled(tmp_path):
    p = _drive_project(tmp_path)
    base = {"out_root": str(tmp_path / "P" / "MyMod"), "p_drive": str(tmp_path / "P"), "mesh_name": "helmet", "states": ["base"], "fbx": False}
    plan = ex.plan_export(p, dict(base, drive_paths=True))
    rv = next(o for o in plan.ops if o.kind == "rvmat")
    assert "P:\\MyMod\\helmet\\data\\helmet_as.paa" in rv.text
    plan = ex.plan_export(p, dict(base))                                 # по умолчанию (API) — без диска
    assert "P:\\" not in next(o for o in plan.ops if o.kind == "rvmat").text and "MyMod\\helmet\\data\\helmet_as.paa" in next(o for o in plan.ops if o.kind == "rvmat").text


def test_drive_paths_roundtrip_through_export_and_autoload(tmp_path):
    from rvmat_studio.core import autoload
    from rvmat_studio.core.project import Project
    p = _drive_project(tmp_path)
    opts = {"out_root": str(tmp_path / "P" / "MyMod"), "p_drive": str(tmp_path / "P"), "mesh_name": "helmet", "states": ["base"], "fbx": False, "drive_paths": True}
    assert not ex.run_export(ex.plan_export(p, opts)).errors
    q = Project("x")
    cand = autoload.scan_folder(tmp_path / "P" / "MyMod" / "helmet")[0]
    autoload.load_candidate(q, cand, [])
    assert q.export_opts["drive_paths"] is True and q.export_opts["prefix"] == "MyMod"
    again = ex.plan_export(q, dict(q.export_opts, p_drive=str(tmp_path / "P")))
    assert "P:\\MyMod\\helmet\\data\\helmet_as.paa" in next(o for o in again.ops if o.kind == "rvmat").text


def test_find_texture_resolves_drive_paths(tmp_path):
    from rvmat_studio.core import texnames
    f = _png(tmp_path / "P" / "MyMod" / "helmet" / "data" / "helmet_as.paa".replace(".paa", ".png"))
    root = tmp_path / "P"
    hit = texnames.find_texture("P:\\MyMod\\helmet\\data\\helmet_as.paa", str(root), None, (), allowed=[str(root)])
    assert hit == f.resolve() or hit == f
    assert texnames.find_texture("P:\\..\\secret\\x.paa", str(root), None, (), allowed=[str(root)]) is None
    assert texnames.to_drive_path(f, root) == "P:\\MyMod\\helmet\\data\\helmet_as.paa"
    assert texnames.to_drive_path(tmp_path / "elsewhere" / "x.png", root) is None


def test_validator_accepts_p_drive_paths_but_not_other_drives():
    from rvmat_studio.backend import Severity, validate
    from rvmat_studio.core.presets import super_template
    m = super_template(); m.stage(4).texture = "P:\\MyMod\\a_as.paa"; m.stage(1).texture = "C:\\x\\a_nohq.paa"
    codes = {(i.code, i.severity) for i in validate(m)}
    assert ("I230", Severity.INFO) in codes and ("E230", Severity.ERROR) in codes
    assert [i for i in validate(m) if i.code == "E230" and "Stage4" in i.location] == []
