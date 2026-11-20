# DayZ RVMAT Studio

ТЗ: [docs/TZ.md](docs/TZ.md).

## Статус

| Этап | Состояние |
|---|---|
| 1. RVMAT Backend | **готов** (`rvmat_studio/backend`) |
| 2. Texture Manager | не начат |
| 3. Material Preview | не начат |
| 4–7. Editor, Presets, Export, Polish | не начаты |

## Backend (Этап 1)
- `parser.py` — lossless-парсер/сериализатор (числа хранятся текстом, неизвестные параметры, классы, комментарии сохраняются; на образцах round-trip побайтно).
- `model.py` — `Material`/`Stage`: общие параметры, Stage1..N, uvTransform, Filter; запись с `.bak`.
- `procedural.py` — `color(...)`, `fresnel(...)`.
- `kit.py` — `KitSpec`: комплект из 6 состояний, общие настройки + переопределения по состояниям, `diff_materials`.
- `validator.py` — ошибки/предупреждения/инфо с путём к параметру (`Stage3.texture`), проверка путей и P:.

```bash
pip install -e .[dev] && pytest
python -m rvmat_studio kit base.rvmat myhelmet out/
python -m rvmat_studio validate file.rvmat
```

## Открытые вопросы
- Нет образца обычного `*.rvmat` (base) — Stage3 для него взят как у decorative и помечен «не подтверждено» (I300).
- `eyemask_destruct.rvmat` не был приложен (4 из 5 файлов).
