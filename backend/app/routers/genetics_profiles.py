"""
Редактор профилей генетики — запись своих профилей (core/genetics_store.py).

  GET    /genetics/{id}  — профиль целиком (тот же формат, что GET /api/v1/genetics/{id})
  PUT    /genetics/{id}  — создать или заменить свой профиль
  DELETE /genetics/{id}  — удалить свой профиль

Чтение — общими GET /genetics (интерфейс) и GET /api/v1/genetics[/{id}]: свои
профили там рядом со встроенными, с "builtin": false. Встроенные менять нельзя
(403): свою версию руководства сохраняют копией под новым id. Запись — под
WRITE_TOKEN, как настройки корпуса: состояние одно на всех посетителей.
"""
from __future__ import annotations

from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel, ConfigDict, Field

from ..api_v1 import genetics_body
from ..core import genetics as genetics_mod
from ..core import genetics_store
from ..core.errors import CalcError
from ..write_access import require_write_token

router = APIRouter()



class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PhaseIn(_Strict):
    temp_by_weight: list[tuple[float, float]] = Field(..., min_length=2, max_length=60)
    min_vent_by_weight: Optional[list[tuple[float, float]]] = Field(None, min_length=2, max_length=60)
    max_vent_by_weight: Optional[list[tuple[float, float]]] = Field(None, min_length=2, max_length=60)
    rh_by_weight: Optional[list[tuple[float, float]]] = Field(None, min_length=2, max_length=60)


class ProfileIn(_Strict):
    # Имя выводится в интерфейсе (через textContent) — длину ограничиваем
    name: str = Field(..., min_length=1, max_length=80)
    setpoint_meaning: Literal["room_temperature", "ventilation_start"]
    nursery_max_kg: float = Field(25.0, gt=3, le=60)
    rh_max_pct: Optional[float] = Field(None, gt=0, le=100)
    source: str = Field("", max_length=200)
    nursery: PhaseIn
    # Нет откорма — одна кривая на обе фазы, как у PIC и Hypor
    finisher: Optional[PhaseIn] = None
    growth_by_day: Optional[list[tuple[float, float]]] = Field(None, min_length=2, max_length=300)


def _ranges(p: ProfileIn) -> None:
    """Пределы значений точек: tuple в pydantic не несёт ограничений на элементы."""
    # Пределы физически возможные, а не «типичные»: опечатку 250 °C или 0 м³/ч
    # отсекаем, необычный, но реальный профиль — нет
    limits = {"temp_by_weight": (5.0, 40.0), "min_vent_by_weight": (0.1, 1000.0),
              "max_vent_by_weight": (0.1, 1000.0), "rh_by_weight": (1.0, 100.0)}
    for phase_name in ("nursery", "finisher"):
        ph = getattr(p, phase_name)
        if ph is None:
            continue
        for curve, (lo, hi) in limits.items():
            for kg, v in getattr(ph, curve) or []:
                if not (0.5 <= kg <= 400 and lo <= v <= hi):
                    raise CalcError("profile_point_out_of_range", phase=phase_name, curve=curve, point=[kg, v])
    for day, kg in p.growth_by_day or []:
        if not (0 <= day <= 400 and 0.5 <= kg <= 400):
            raise CalcError("profile_point_out_of_range", curve="growth_by_day", point=[day, kg])


@router.get("/genetics/{profile_id}")
def get_profile(profile_id: str = Path(..., pattern=genetics_store.PROFILE_ID_PATTERN)):
    # Интерфейсу — без API-ключа: редактор открывает профиль, чтобы сохранить копию
    p = genetics_mod.find(profile_id)
    if p is None:
        raise HTTPException(status_code=404, detail={"code": "genetics_not_found", "params": {"id": profile_id}})
    return genetics_body(p)


@router.put("/genetics/{profile_id}", dependencies=[Depends(require_write_token)])
def put_profile(payload: ProfileIn, profile_id: str = Path(..., pattern=genetics_store.PROFILE_ID_PATTERN)):
    try:
        _ranges(payload)
        p = genetics_store.save(profile_id, payload.model_dump(exclude_none=True))
    except genetics_store.BuiltinProfileReadOnly:
        raise HTTPException(status_code=403, detail={"code": "builtin_profile_read_only", "params": {"id": profile_id}})
    except CalcError as e:
        raise HTTPException(status_code=422, detail=e.detail)
    return {"id": p.id, "name": p.name, "builtin": False}


@router.delete("/genetics/{profile_id}", dependencies=[Depends(require_write_token)])
def delete_profile(profile_id: str = Path(..., pattern=genetics_store.PROFILE_ID_PATTERN)):
    try:
        genetics_store.delete(profile_id)
    except genetics_store.BuiltinProfileReadOnly:
        raise HTTPException(status_code=403, detail={"code": "builtin_profile_read_only", "params": {"id": profile_id}})
    except CalcError as e:
        raise HTTPException(status_code=404, detail=e.detail)
    return {"deleted": profile_id}
