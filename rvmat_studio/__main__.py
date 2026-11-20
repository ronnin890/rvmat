"""CLI: python -m rvmat_studio kit <base.rvmat> <name> <outdir> | validate <file.rvmat>"""
import sys
from pathlib import Path

from .backend import KitSpec, Material, State, validate, validate_kit


def main(argv=None) -> int:
    a = list(sys.argv[1:] if argv is None else argv)
    if len(a) >= 2 and a[0] == "validate":
        issues = validate(Material.load(a[1]))
        for i in issues:
            print(i)
        return 1 if any(i.severity.name == "ERROR" for i in issues) else 0
    if len(a) == 4 and a[0] == "kit":
        spec = KitSpec(a[2], Material.load(a[1]))
        kit = spec.render_all()
        out = Path(a[3])
        for fn, m in kit.items():
            m.save(out / fn)
            print("written", out / fn)
        for i in validate_kit(kit, a[2]):
            print(i)
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
