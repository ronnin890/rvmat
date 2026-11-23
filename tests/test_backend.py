from pathlib import Path

import pytest

from rvmat_studio.backend import (KitSpec, Material, RvmatParseError, Severity, State,
                                  diff_materials, kit_filename, parse, serialize, validate,
                                  validate_kit)
from rvmat_studio.backend.procedural import parse_procedural, check_procedural
from rvmat_studio.backend.parser import format_number

S = Path(__file__).parent / "samples"
FILES = sorted(S.glob("*.rvmat"))


@pytest.mark.parametrize("f", FILES, ids=lambda f: f.name)
def test_roundtrip_is_byte_exact(f):
    raw = f.read_bytes().decode()
    assert serialize(parse(raw)) == raw


@pytest.mark.parametrize("f", FILES, ids=lambda f: f.name)
def test_samples_validate_clean(f):
    assert [i for i in validate(Material.load(f)) if i.severity >= Severity.WARNING] == []


def test_unknown_params_and_comments_preserved():
    src = ('// hi\r\nambient[]={1,1,1,1};\r\ncustomThing="x";\r\nclass Foo : Bar\r\n{\r\n\tk[]={1,{2,3}};\r\n};\r\n')
    m = Material.from_text(src)
    m.set_scalar("specularPower", 20)
    out = m.to_text()
    assert "// hi" in out and 'customThing="x"' in out and "class Foo : Bar" in out and "{1,{2,3}}" in out


def test_parse_errors_have_position():
    with pytest.raises(RvmatParseError) as e:
        parse("class A {\r\nx=1;\r\n")
    assert "скобка" in str(e.value)
    with pytest.raises(RvmatParseError):
        parse("a[]=1;")


def test_number_format_matches_dayz():
    assert format_number(0.9) == "0.89999998"
    assert format_number(0.8) == "0.80000001"
    assert format_number(1) == "1" and format_number(0.5) == "0.5" and format_number(15.0) == "15"


def test_procedural():
    p = parse_procedural("#(ai,32,128,1)fresnel(0.88,1.89)")
    assert p.fresnel == (0.88, 1.89)
    assert parse_procedural("#(argb,8,8,3)color(0,0,0,0,MC)").tag == "MC"
    assert check_procedural("#(argb,8,8,3)color(0,0,0,MC)")


def test_edit_keeps_everything_else():
    m = Material.load(S / "eyemask_damage.rvmat")
    before = m.to_text()
    m.stage(6).texture = "#(ai,32,128,1)fresnel(1,2)"
    assert len(diff_materials(Material.from_text(before), m)) == 1


# --- комплект: генератор должен воспроизводить образцы побайтно
@pytest.fixture
def decorative_kit():
    return KitSpec("eyemask", Material.load(S / "eyemask_decorative.rvmat"))


@pytest.mark.parametrize("state", [State.DECORATIVE, State.DECORATIVE_DAMAGE, State.DECORATIVE_DESTRUCT])
def test_generator_reproduces_decorative_samples(decorative_kit, state):
    got = decorative_kit.render(state).to_text()
    assert got == (S / kit_filename("eyemask", state)).read_bytes().decode()


def test_generator_damage_matches_sample_structure(decorative_kit):
    # eyemask_damage использует другие текстуры (black), поэтому сравниваем только Stage3
    base = Material.load(S / "eyemask_damage.rvmat")
    dm = KitSpec("eyemask", base).render(State.DAMAGE)
    assert dm.to_text() == base.to_text()


def test_overrides_and_propagation(decorative_kit):
    decorative_kit.overrides[State.DAMAGE] = {"specularPower": 30, "Stage1.texture": r"dz\x\y_nohq.paa"}
    decorative_kit.shared.set_scalar("specularPower", 10)   # общее — на все
    assert decorative_kit.render(State.DESTRUCT).get("specularPower") == 10
    d = decorative_kit.render(State.DAMAGE)
    assert d.get("specularPower") == 30 and d.stage(1).texture == r"dz\x\y_nohq.paa"
    assert {k for k, *_ in diff_materials(decorative_kit.render(State.BASE), d)} == \
        {"specularPower", "Stage1.texture", "Stage3.texture"}


def test_full_kit_names_and_validation(decorative_kit):
    kit = decorative_kit.render_all()
    assert list(kit) == ["eyemask.rvmat", "eyemask_damage.rvmat", "eyemask_destruct.rvmat",
                         "eyemask_decorative.rvmat", "eyemask_decorative_damage.rvmat",
                         "eyemask_decorative_destruct.rvmat"]
    issues = validate_kit(kit, "eyemask")
    assert not [i for i in issues if i.severity >= Severity.WARNING]
    del kit["eyemask_damage.rvmat"]
    assert any(i.code == "E400" for i in validate_kit(kit, "eyemask"))


def test_validator_catches_problems(tmp_path):
    m = Material.load(S / "eyemask_damage.rvmat")
    m.root.remove("specular")
    m.stage(1).texture = r"C:\x\a.tga"
    m.stage(5).texture = r"dz\none\missing.paa"
    m.stage(6).texture = "#(ai,32,128,1)fresnel(1)"
    m.stage(2).node.remove("uvTransform")
    codes = {i.code: i for i in validate(m, tmp_path)}
    assert {"E100", "E230", "W233", "E211", "E220"} <= set(codes)
    assert codes["E211"].location == "Stage6.texture"
    assert validate(m, tmp_path)[0].severity == Severity.ERROR


def test_save_creates_backup(tmp_path):
    m = Material.load(S / "eyemask_damage.rvmat")
    p = tmp_path / "a.rvmat"
    m.save(p)
    m.set_scalar("specularPower", 1)
    m.save(p)
    assert (tmp_path / "a.rvmat.bak").read_text() != p.read_text()


def test_validator_accepts_source_image_instead_of_paa(tmp_path):
    from rvmat_studio.backend import Severity, validate
    from rvmat_studio.backend.model import Material
    (tmp_path / "dz").mkdir()
    (tmp_path / "dz" / "x_as.png").write_bytes(b"png")
    m = Material.from_text((Path(__file__).parent / "samples" / "eyemask_damage.rvmat").read_text())
    m.stage(4).texture = "dz\\X_AS.paa"                                    # регистр не важен, вместо .paa лежит .png
    codes = {i.code: i for i in validate(m, tmp_path) if i.location == "Stage4.texture"}
    assert "W233" not in codes and codes["I233"].severity == Severity.INFO
    m.stage(4).texture = "dz\\nothing.paa"
    assert any(i.code == "W233" for i in validate(m, tmp_path))
