"""
Пользовательские профили генетики — хранилище (пункт «редактор профилей»).

По образцу MINMAXapp (core/growth_profiles.py, 59390c7): один профиль — один
JSON-файл, встроенные профили менять и удалять нельзя, своя версия кросса —
копия под новым id. Отличие от птичника: встроенные профили PigMaxApp живут в
коде (core/genetics.py) — у каждого пометки о первоисточнике и допущения сшивки
таблиц, в JSON их не перевести без потери. Поэтому на диске только свои.

Формат файла — тот же, что отдаёт GET /api/v1/genetics/{id}: профиль, прочитанный
из API, можно поправить и сохранить как свой без перекладки полей.

  {"name": "...", "setpoint_meaning": "room_temperature", "nursery_max_kg": 25,
   "rh_max_pct": 70, "source": "свободный текст",
   "nursery": {"temp_by_weight": [[кг, °C], ...], "min_vent_by_weight": ..., ...},
   "finisher": {...},
   "growth_by_day": [[день, кг], ...]}
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Optional

from .errors import CalcError
from .json_store import atomic_write_json
from .paths import data_path

log = logging.getLogger(__name__)

# id попадает в имя файла: без точек и слэшей нельзя выйти из каталога
PROFILE_ID_PATTERN = r"^[a-z0-9][a-z0-9_]{0,63}$"
CURVES = ("temp_by_weight", "min_vent_by_weight", "max_vent_by_weight", "rh_by_weight")


class BuiltinProfileReadOnly(PermissionError):
    """Попытка изменить или удалить профиль из руководства селекционера."""


def profiles_dir() -> Path:
    # Путь читаем при каждом обращении: тесты и площадки подменяют его через env,
    # и рабочий каталог тестами не трогается (как PIG_SETTINGS_JSON)
    return data_path("PIG_GENETICS_DIR", "backend/data/genetics_profiles")


def _check_id(profile_id: str) -> None:
    if not re.fullmatch(PROFILE_ID_PATTERN, profile_id or ""):
        raise CalcError("profile_id_invalid", id=str(profile_id))


def _increasing(points: list, code: str, **params) -> None:
    # Интерполяция (и обратная — день по весу) требует строго растущего аргумента:
    # повтор веса дал бы деление на ноль, обратный порядок — молча неверную уставку
    xs = [p[0] for p in points]
    if any(b <= a for a, b in zip(xs, xs[1:])):
        raise CalcError(code, **params)


def validate(raw: dict) -> None:
    """Проверки, которые не выразить схемой полей: порядок точек и рост кривой роста."""
    for phase in ("nursery", "finisher"):
        for curve in CURVES:
            pts = (raw.get(phase) or {}).get(curve)
            if pts:
                _increasing(pts, "profile_curve_not_increasing", phase=phase, curve=curve)
    growth = raw.get("growth_by_day")
    if growth:
        _increasing(growth, "profile_growth_not_increasing", what="day")
        # Вес по дням тоже строго растёт: проект ищет день по весу обратной функцией
        _increasing([(w, d) for d, w in growth], "profile_growth_not_increasing", what="weight")


def _profile(profile_id: str, raw: dict):
    from . import genetics as G      # genetics ищет профиль через этот модуль — импорт здесь

    def phase(d: Optional[dict]) -> Optional[G.PhaseCurves]:
        if not d:
            return None
        return G.PhaseCurves(**{c: tuple(tuple(p) for p in d[c]) if d.get(c) else None for c in CURVES})

    nursery = phase(raw.get("nursery"))
    finisher = phase(raw.get("finisher")) or nursery
    growth = raw.get("growth_by_day")
    return G.GeneticsProfile(
        # id — из имени файла, а не из содержимого: файл не может выдать себя за другой
        id=profile_id, name=raw.get("name") or profile_id, source_id="user_profile",
        setpoint_meaning=raw["setpoint_meaning"], nursery=nursery, finisher=finisher,
        growth=tuple(tuple(p) for p in growth) if growth else None,
        rh_max_pct=raw.get("rh_max_pct"),
        # Числа ввёл пользователь — это должно быть видно в каждом ответе по профилю
        assumptions=({"code": "user_profile", "params": {"id": profile_id, "source": raw.get("source") or ""}},),
        nursery_max_kg=raw.get("nursery_max_kg") or G.DEFAULT_NURSERY_MAX_KG,
        builtin=False,
    )


def load(profile_id: str):
    """Свой профиль или None. Битый файл — None с записью в лог, расчёт не падает с 500."""
    if not re.fullmatch(PROFILE_ID_PATTERN, profile_id or ""):
        return None
    path = profiles_dir() / f"{profile_id}.json"
    if not path.is_file():
        return None
    try:
        return _profile(profile_id, json.loads(path.read_text(encoding="utf-8")))
    except Exception:
        log.exception("genetics profile unreadable, skipped: %s", path)
        return None


def list_custom() -> list:
    d = profiles_dir()
    if not d.is_dir():
        return []
    out = [load(p.stem) for p in sorted(d.glob("*.json"))]
    return [p for p in out if p is not None]


def save(profile_id: str, raw: dict[str, Any]):
    from . import genetics as G
    _check_id(profile_id)
    if profile_id in G.PROFILES:
        raise BuiltinProfileReadOnly(profile_id)
    validate(raw)
    atomic_write_json(profiles_dir() / f"{profile_id}.json", raw)
    return load(profile_id)


def delete(profile_id: str) -> None:
    from . import genetics as G
    _check_id(profile_id)
    if profile_id in G.PROFILES:
        raise BuiltinProfileReadOnly(profile_id)
    path = profiles_dir() / f"{profile_id}.json"
    if not path.is_file():
        raise CalcError("genetics_not_found", id=profile_id)
    path.unlink()
