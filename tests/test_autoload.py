"""Загрузка проекта из папки <меш>/(<меш>.fbx, data/): обратная операция к экспорту."""
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from rvmat_studio.backend import Material, State
from rvmat_studio.core import autoload, export as ex
from rvmat_studio.core import fbx, meshes
from rvmat_studio.core.project import Project

OBJ = ("v 0 0 0\nv 2 0 0\nv 2 2 0\nv 0 2 0\nv 4 0 0\nv 4 2 0\ng body\nf 1 2 3 4\ng visor\nf 2 5 6 3\n")


def png(path, rgba, size=16):
    path.parent.mkdir(parents=True, exist_ok=True)
    a = np.zeros((size, size, 4), np.uint8); a[...] = rgba; a[:, : size // 2, 0] = 20
    Image.fromarray(a).save(path)
    return path


@pytest.fixture
def exported(tmp_path):
    """Реальный результат экспорта: <P>/MyMod/helmet/{helmet.fbx, data/*} с двумя материалами и пресетом."""
    src = tmp_path / "src"
    p = Project("helmet")
    p.project_root = str(tmp_path / "P")
    for suf, rgba in (("nohq", (128, 128, 255, 255)), ("smdi", (255, 200, 90, 255)), ("as", (255, 150, 255, 255))):
        png(src / f"h_{suf}.png", rgba)
        p.tex_map[f"src\\h_{suf}.paa"] = str(src / f"h_{suf}.png")
    png(src / "h_co.png", (120, 80, 200, 255)); png(src / "v_co.png", (10, 200, 10, 255)); png(src / "v_nohq.png", (128, 128, 255, 255))
    p.tex_map["src\\v_nohq.paa"] = str(src / "v_nohq.png")
    for stage, suf in ((1, "nohq"), (5, "smdi"), (4, "as")):
        p.spec.shared.stage(stage).texture = f"src\\h_{suf}.paa"
    p.set_value("shared", "specularPower", 33)
    p.set_value("damage", "ambient", [0.3, 0.3, 0.3, 1])
    p.set_value("decorative", "Stage4.texture", "src\\h_as.paa")
    p.set_base_color(str(src / "h_co.png"))
    (src / "helmet.obj").write_text(OBJ)
    p.model_path = str(src / "helmet.obj")
    p.add_slot("glass")
    p.slot.preset_tag = "glass"
    p.assign_part("visor", "glass")
    p.slot.spec.shared.stage(1).texture = "src\\v_nohq.paa"
    p.set_value("shared", "specularPower", 90)
    p.set_base_color(str(src / "v_co.png"))
    opts = {"out_root": str(tmp_path / "P" / "MyMod"), "p_drive": str(tmp_path / "P"), "mesh_name": "helmet",
            "states": ["base", "damage", "destruct", "decorative"]}
    res = ex.run_export(ex.plan_export(p, opts))
    assert not res.errors
    return tmp_path / "P" / "MyMod" / "helmet", p, tmp_path


def test_scan_finds_mesh_folder_itself_or_inside_parent(exported):
    mesh_dir, _, tmp = exported
    c = autoload.scan_folder(mesh_dir)                                   # выбрана сама папка меша
    assert len(c) == 1 and c[0].mesh_dir == mesh_dir and c[0].model.name == "helmet.fbx" and c[0].data_dir.name == "data"
    assert len(c[0].rvmats) == 8 and len(c[0].textures) >= 6
    for parent in (tmp / "P" / "MyMod", tmp / "P"):                      # выбран родитель (корень с папкой меша внутри)
        c2 = autoload.scan_folder(parent)
        assert [x.mesh_dir for x in c2] == [mesh_dir]
    assert autoload.scan_folder(tmp / "src") == [] or autoload.scan_folder(tmp / "src")[0].model.name == "helmet.obj"


def test_scan_ignores_unrelated_and_limits_depth(tmp_path):
    (tmp_path / "a" / "b" / "c" / "d" / "e").mkdir(parents=True)
    (tmp_path / "a" / "b" / "c" / "d" / "e" / "deep.fbx").write_bytes(b"x")
    assert autoload.scan_folder(tmp_path) == []                          # слишком глубоко — не сканируем весь диск
    (tmp_path / "x").mkdir(); (tmp_path / "x" / "readme.txt").write_text("hi")
    assert autoload.scan_folder(tmp_path / "x") == []
    (tmp_path / "m").mkdir(); (tmp_path / "m" / "data").mkdir(); (tmp_path / "m" / "data" / "m.rvmat").write_text("ambient[]={1,1,1,1};")
    only_data = autoload.scan_folder(tmp_path / "m")
    assert len(only_data) == 1 and only_data[0].model is None and len(only_data[0].rvmats) == 1


def test_load_restores_materials_parts_presets_textures(exported):
    mesh_dir, orig, tmp = exported
    cand = autoload.scan_folder(mesh_dir)[0]
    mesh = fbx.load_fbx(cand.model)
    p = Project("fresh")
    assert autoload.is_pristine(p)
    msgs = autoload.load_candidate(p, cand, mesh.part_names)
    assert [s.name for s in p.slots] == ["helmet", "helmet_visor_glass"]
    assert p.part_map == {"visor": "helmet_visor_glass"} and p.slots[1].label == "visor" and p.slots[1].preset_tag == "glass"
    assert p.slots[0].spec.shared.get("specularPower") == 33 and p.slots[1].spec.shared.get("specularPower") == 90
    assert p.slots[0].spec.overrides[State.DAMAGE]["ambient"][0] == pytest.approx(0.3, abs=1e-5)       # отличие состояния сохранено
    assert p.model_path == str(cand.model) and p.mesh_name == "helmet" and p.title == "helmet" and p.dirty
    assert Path(p.slots[0].base_color).name == "helmet_co.paa" and Path(p.slots[1].base_color).name == "helmet_visor_co.paa"
    assert len(p.textures) == len(cand.textures)
    assert p.export_opts["out_root"] == str(mesh_dir.parent) and p.export_opts["prefix"] == "MyMod" and set(p.export_opts["states"]) == {"base", "damage", "destruct", "decorative"}
    assert p.project_root == str(tmp / "P")                                  # корень P: выведен из путей в RVMAT
    assert any("Загружено" in m for m in msgs)
    # игровые пути текстур из RVMAT находятся на диске
    from rvmat_studio.core.texnames import find_texture
    t = p.slots[0].spec.shared.stage(1).texture
    assert t == "MyMod\\helmet\\data\\helmet_nohq.paa" and Path(find_texture(t, p.project_root, p.tex_map)).name == "helmet_nohq.paa"


def test_load_is_one_undo_step(exported):
    mesh_dir, _, _ = exported
    p = Project("fresh")
    autoload.load_candidate(p, autoload.scan_folder(mesh_dir)[0], ["body", "visor"])
    assert len(p.slots) == 2
    p.undo()
    assert len(p.slots) == 1 and p.model_path == "" and not p.textures


def test_roundtrip_export_load_export_is_identical(exported):
    mesh_dir, orig, tmp = exported
    cand = autoload.scan_folder(mesh_dir)[0]
    p = Project("x")
    autoload.load_candidate(p, cand, fbx.load_fbx(cand.model).part_names)
    out2 = tmp / "again"
    opts = dict(p.export_opts); opts.update({"out_root": str(out2), "p_drive": str(tmp / "P"), "prefix": "MyMod"})
    res = ex.run_export(ex.plan_export(p, opts))
    assert not res.errors
    a = sorted((x.relative_to(mesh_dir) for x in mesh_dir.rglob("*") if x.is_file() and not x.name.endswith(".bak")))
    b = sorted((x.relative_to(out2 / "helmet") for x in (out2 / "helmet").rglob("*") if x.is_file()))
    assert a == b                                                           # те же файлы и имена
    for rel in a:
        if rel.suffix == ".rvmat":
            assert (mesh_dir / rel).read_text() == (out2 / "helmet" / rel).read_text(), rel     # те же RVMAT с теми же путями
    assert fbx.load_fbx(out2 / "helmet" / "helmet.fbx").part_names == ["body", "visor"]


def test_state_names_and_preset_without_selection(tmp_path):
    d = tmp_path / "gear"; (d / "data").mkdir(parents=True)
    from rvmat_studio.core.presets import super_template
    for name in ("gear_metal_polished", "gear_metal_polished_damage"):
        super_template().save(d / "data" / f"{name}.rvmat")
    (d / "gear.obj").write_text(OBJ)
    c = autoload.scan_folder(d)[0]
    p = Project("t")
    autoload.load_candidate(p, c, [])
    assert [s.name for s in p.slots] == ["gear_metal_polished"] and p.slots[0].preset_tag == "metal_polished"
    assert set(p.export_opts["states"]) == {"base", "damage"}
    assert autoload.split_state("a_decorative_damage") == ("a", State.DECORATIVE_DAMAGE) and autoload.split_state("a_damage")[1] == State.DAMAGE


def test_rvmat_with_bad_syntax_is_reported_not_fatal(tmp_path):
    d = tmp_path / "m"; (d / "data").mkdir(parents=True)
    (d / "data" / "m.rvmat").write_text("class Broken {")
    from rvmat_studio.core.presets import super_template
    super_template().save(d / "data" / "m_other.rvmat")
    p = Project("t")
    msgs = autoload.load_candidate(p, autoload.scan_folder(d)[0], [])
    assert any("не удалось разобрать" in m for m in msgs) and len(p.slots) == 1
