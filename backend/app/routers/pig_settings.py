"""
pig_settings.py — GET/PUT endpoints for building and heater configuration.
"""

from dataclasses import asdict
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from ..core.building_thermal import BuildingDimensions, CalibrationTest, calibration_problem
from ..core.cooling_fit import fit_cooling_curve
from ..core.errors import CalcError
from ..core.pig_settings import PigSettings, BuildingConfig, HeaterConfig, load_settings, save_settings
from ..write_access import require_write_token

router = APIRouter()


class CoolingPoint(BaseModel):
    t_s: float = Field(..., ge=0, description="Секунды от старта теста")
    temp_c: float = Field(..., ge=-30, le=50)


class CalibrationIn(BaseModel):
    """Тест остывания ПУСТОГО прогретого корпуса: отметка на каждом градусе падения."""
    t_outside_c: float = Field(..., ge=-50, le=40)
    points: list[CoolingPoint] = Field(..., min_length=2, max_length=60)


class SettingsPayload(BaseModel):
    building:       BuildingConfig | None = None
    heater:         HeaterConfig   | None = None
    ua_coefficient: float | None         = None
    calibration:    CalibrationIn  | None = None


class CalibrateRequest(CalibrationIn):
    volume_m3: Optional[float] = Field(None, gt=0, description="Нет — объём из сохранённого корпуса")


def _fit(volume_m3: float, cal: CalibrationIn):
    try:
        return fit_cooling_curve(volume_m3, cal.t_outside_c, [(p.t_s, p.temp_c) for p in cal.points])
    except CalcError as e:
        raise HTTPException(status_code=422, detail=e.detail)


@router.post("/calc/calibrate-ua")
def calibrate_ua(req: CalibrateRequest):
    """
    UA по кривой остывания (перенос MINMAXapp /thermal/calibrate-curve): модель
    «воздух + конструкции». Только считает; сохранение — PUT /settings с calibration.
    """
    volume = req.volume_m3 or load_settings().building.volume_m3
    fit = _fit(volume, req)
    return {"ua_coefficient": round(fit.ua_w_per_k, 1), "volume_m3": round(volume, 1),
            "c_air_j_per_k": round(fit.c_air_j_per_k), "c_struct_j_per_k": round(fit.c_struct_j_per_k),
            "c_struct_resolved": fit.c_struct_resolved, "rms_c": round(fit.rms_c, 3),
            "duration_minutes": round(fit.duration_s / 60.0, 1), "warnings": fit.warnings}


@router.get("/settings")
def get_settings():
    s = load_settings()
    return {
        **s.model_dump(),
        "building": {
            **s.building.model_dump(),
            "volume_m3":    round(s.building.volume_m3, 1),
            "floor_area_m2": round(s.building.floor_area_m2, 1),
        }
    }


@router.put("/settings", dependencies=[Depends(require_write_token)])
def put_settings(payload: SettingsPayload):
    s = load_settings()
    if payload.building is not None:
        s.building = payload.building
    if payload.heater is not None:
        s.heater = payload.heater
    if payload.ua_coefficient is not None:
        s.ua_coefficient = payload.ua_coefficient
        # UA задан вручную (по конструкции) — прежняя калибровка к нему уже не
        # относится: запись теста с другим UA читалась бы как подтверждение числа
        if payload.calibration is None:
            s.calibration = None
    if payload.calibration is not None:
        # Пересчёт по отметкам и те же проверки, что в MINMAXapp: короткий тест,
        # неправдоподобный U оболочки, неразличимый UA — в расчёт не пускаем
        b = s.building
        fit = _fit(b.volume_m3, payload.calibration)
        pts = [[p.t_s, p.temp_c] for p in payload.calibration.points]
        cal = CalibrationTest(t_initial_c=pts[0][1], t_final_c=pts[-1][1],
                              t_outside_c=payload.calibration.t_outside_c, time_minutes=pts[-1][0] / 60.0,
                              ua_coefficient=fit.ua_w_per_k, method="cooling_curve", points=pts)
        problem = calibration_problem(cal, BuildingDimensions(b.length_m, b.width_m, b.height_m))
        if problem:
            raise HTTPException(status_code=422, detail=problem)
        if "ua_not_resolved" in fit.warnings:
            raise HTTPException(status_code=422, detail={"code": "calibration_ua_unresolved", "params": {}})
        s.ua_coefficient = round(fit.ua_w_per_k, 1)
        s.calibration = asdict(cal)
    save_settings(s)
    return get_settings()
