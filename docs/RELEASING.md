# Выпуск версий

Разработка идёт в рабочих ветках (`claude/...`), **готовые промежуточные версии (0.3, 0.4, ...) выкладываются в ветку `release`**
и помечаются тегом `vX.Y.Z`. Тег запускает workflow `release`: Windows-сборка, установщик, портативный zip, SHA-256, GitHub Release.
Любой push в `release` без тега тоже собирает установщик (как артефакт workflow), но релиз не публикует.

```bash
python scripts/bump_version.py 0.4.0         # версия в __init__.py, pyproject.toml, installer.iss
# добавить запись в CHANGELOG.md, прогнать тесты
git commit -am "Версия 0.4.0"
git checkout release && git merge --no-ff <рабочая-ветка> -m "Релиз 0.4.0"
git tag -a v0.4.0 -m "0.4.0" && git push origin release v0.4.0
```
