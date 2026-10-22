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
from ..core import design_climate
from ..core import genetics as genetics_mod
from ..core.pig_settings import PigSettings, BuildingConfig, FarmConfig, HeaterConfig, load_settings, save_settings
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
    farm:           FarmConfig     | None = None


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
    if payload.farm is not None:
        # Несуществующий профиль по умолчанию сохранился бы с 200, а потом формы
        # открывались бы с пустым выбором — проверяем сразу (как MINMAXapp app-config)
        if genetics_mod.find(payload.farm.default_genetics) is None:
            raise HTTPException(status_code=422, detail={"code": "genetics_not_found",
                                                         "params": {"id": payload.farm.default_genetics}})
        s.farm = payload.farm
    save_settings(s)
    return get_settings()


class DesignClimateRequest(BaseModel):
    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)


@router.post("/settings/design-climate", dependencies=[Depends(require_write_token)])
def put_design_climate(req: DesignClimateRequest):
    # Source: MINMAXapp routers/settings.py -> put_design_climate (ADAPT: поля FarmConfig)
    """
    Расчётный климат по координатам фермы: 10 лет ERA5 (Open-Meteo) → 99.6 % / 0.4 %
    по ASHRAE, гл. 14; сохраняется в настройках хозяйства. Под токеном записи: каждый
    вызов — 10 лет часовых данных из чужой квоты, перебором её не исчерпать.
    """
    try:
        temps, rhs, elevation = design_climate.fetch_hourly(req.latitude, req.longitude)
        dc = design_climate.design_conditions(temps, rhs, elevation)
    except CalcError as e:
        raise HTTPException(status_code=502, detail=e.detail)
    s = load_settings()
    f = s.farm
    f.latitude, f.longitude = req.latitude, req.longitude
    f.winter_design_c, f.summer_design_db_c = dc.heating_996_c, dc.cooling_04_db_c
    f.summer_design_rh_pct, f.summer_mcwb_c = dc.cooling_04_rh_pct, dc.cooling_04_mcwb_c
    f.evap_wb_c, f.climate_source = dc.evap_04_wb_c, dc.source
    # Высоту фермы не трогаем, если её уже задали; иначе — высота точки реанализа
    if not f.altitude_m:
        f.altitude_m = float(dc.elevation_m)
    save_settings(s)
    return get_settings()
