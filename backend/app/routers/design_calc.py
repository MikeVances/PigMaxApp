"""
design_calc.py — проектный расчёт для интерфейса (страница design.html).

Тот же расчёт, что POST /api/v1/ventilation-designs (общая функция run_design), но
маршрутом интерфейса: API v1 с заданными API_KEYS требует ключ, которого у
браузера посетителя нет. Формат ошибок — как у остальных маршрутов интерфейса
(detail с кодом), а не RFC 7807 API v1.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..api_v1 import DesignRequest, run_design
from ..core.errors import CalcError
from ..core.pig_heat import MethodNotApplicable

router = APIRouter()

_NOT_FOUND = {"genetics_not_found", "material_not_found"}


@router.post("/calc/design")
def calc_design(req: DesignRequest):
    try:
        d = run_design(req)
    except CalcError as e:
        raise HTTPException(status_code=404 if e.code in _NOT_FOUND else 422, detail=e.detail)
    except MethodNotApplicable as e:
        raise HTTPException(status_code=422, detail={"code": "method_not_applicable", "params": {"message": str(e)}})
    return {"envelope": d.envelope, "days": d.days, "equipment": d.equipment,
            "sources": d.sources, "assumptions": d.assumptions, "warnings": d.warnings}
