"""
pig_settings.py — настройки фермы: корпуса (размеры, отопитель, UA, калибровка)
и хозяйство (высота, падёж, генетика, расчётный климат).

Корпус выбирается параметром ?house=<имя>; без него — первый корпус, как до
этапа 9, когда корпус был один. Список корпусов хранит сервер, а не браузер:
у MINMAXapp список в браузере потребовал отдельного переноса калибровки при
переименовании — здесь переименование и есть перенос.
"""

from dataclasses import asdict
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel, Field
from ..core.building_thermal import BuildingDimensions, CalibrationTest, calibration_problem
from ..core.cooling_fit import fit_cooling_curve
from ..core.errors import CalcError
from ..core import design_climate
from ..core import genetics as genetics_mod
from ..core.house_id import HOUSE_ID_PATTERN
from ..core.pig_settings import (PigSettings, BuildingConfig, FarmConfig, HeaterConfig, HouseConfig,
                                 load_settings, save_settings)
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


HouseQuery = Query(None, min_length=1, max_length=64, pattern=HOUSE_ID_PATTERN, description="Корпус; нет — первый")


def _house(s: PigSettings, house: Optional[str]) -> tuple[str, HouseConfig]:
    hid = s.house_id(house)
    if hid not in s.houses:
        raise HTTPException(status_code=404, detail={"code": "house_not_found", "params": {"house_id": hid}})
    return hid, s.houses[hid]


def _fit(volume_m3: float, cal: CalibrationIn):
    try:
        return fit_cooling_curve(volume_m3, cal.t_outside_c, [(p.t_s, p.temp_c) for p in cal.points])
    except CalcError as e:
        raise HTTPException(status_code=422, detail=e.detail)


@router.post("/calc/calibrate-ua")
def calibrate_ua(req: CalibrateRequest, house: Optional[str] = HouseQuery):
    """
    UA по кривой остывания (перенос MINMAXapp /thermal/calibrate-curve): модель
    «воздух + конструкции». Только считает; сохранение — PUT /settings с calibration.
    """
    volume = req.volume_m3 or _house(load_settings(), house)[1].building.volume_m3
    fit = _fit(volume, req)
    return {"ua_coefficient": round(fit.ua_w_per_k, 1), "volume_m3": round(volume, 1),
            "c_air_j_per_k": round(fit.c_air_j_per_k), "c_struct_j_per_k": round(fit.c_struct_j_per_k),
            "c_struct_resolved": fit.c_struct_resolved, "rms_c": round(fit.rms_c, 3),
            "duration_minutes": round(fit.duration_s / 60.0, 1), "warnings": fit.warnings}


@router.get("/settings")
def get_settings(house: Optional[str] = HouseQuery):
    s = load_settings()
    hid, h = _house(s, house)
    # Поля корпуса — на верхнем уровне, как до этапа 9: страницы читают s.building,
    # s.heater, s.ua_coefficient; рядом — какой это корпус и какие ещё есть
    return {
        "house_id": hid, "houses": list(s.houses),
        **h.model_dump(),
        "building": {
            **h.building.model_dump(),
            "volume_m3":    round(h.building.volume_m3, 1),
            "floor_area_m2": round(h.building.floor_area_m2, 1),
        },
        "farm": s.farm.model_dump(),
    }


@router.put("/settings", dependencies=[Depends(require_write_token)])
def put_settings(payload: SettingsPayload, house: Optional[str] = HouseQuery):
    s = load_settings()
    hid, h = _house(s, house)
    if payload.building is not None:
        h.building = payload.building
    if payload.heater is not None:
        h.heater = payload.heater
    if payload.ua_coefficient is not None:
        h.ua_coefficient = payload.ua_coefficient
        # UA задан вручную (по конструкции) — прежняя калибровка к нему уже не
        # относится: запись теста с другим UA читалась бы как подтверждение числа
        if payload.calibration is None:
            h.calibration = None
    if payload.calibration is not None:
        # Пересчёт по отметкам и те же проверки, что в MINMAXapp: короткий тест,
        # неправдоподобный U оболочки, неразличимый UA — в расчёт не пускаем
        b = h.building
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
        h.ua_coefficient = round(fit.ua_w_per_k, 1)
        h.calibration = asdict(cal)
    if payload.farm is not None:
        # Несуществующий профиль по умолчанию сохранился бы с 200, а потом формы
        # открывались бы с пустым выбором — проверяем сразу (как MINMAXapp app-config)
        if genetics_mod.find(payload.farm.default_genetics) is None:
            raise HTTPException(status_code=422, detail={"code": "genetics_not_found",
                                                         "params": {"id": payload.farm.default_genetics}})
        s.farm = payload.farm
    save_settings(s)
    return get_settings(hid)


# ─── Корпуса фермы ────────────────────────────────────────────────────────────

class HouseNew(BaseModel):
    house_id: str = Field(..., min_length=1, max_length=64, pattern=HOUSE_ID_PATTERN)
    # Копировать размеры и отопитель — типовые корпуса фермы одинаковы. UA и
    # калибровку не копируем: это ИЗМЕРЕНИЯ другого корпуса, а не его проект
    copy_from: Optional[str] = Field(None, min_length=1, max_length=64, pattern=HOUSE_ID_PATTERN)


class HouseRename(BaseModel):
    new_house_id: str = Field(..., min_length=1, max_length=64, pattern=HOUSE_ID_PATTERN)


def _taken(hid: str):
    return HTTPException(status_code=409, detail={"code": "house_exists", "params": {"house_id": hid}})


@router.post("/houses", dependencies=[Depends(require_write_token)])
def create_house(req: HouseNew):
    s = load_settings()
    if req.house_id in s.houses:
        raise _taken(req.house_id)
    new = HouseConfig()
    if req.copy_from is not None:
        _, src = _house(s, req.copy_from)
        new = HouseConfig(building=src.building.model_copy(), heater=src.heater.model_copy())
    s.houses[req.house_id] = new
    save_settings(s)
    return get_settings(req.house_id)


@router.post("/houses/{house_id}/rename", dependencies=[Depends(require_write_token)])
def rename_house(req: HouseRename, house_id: str = Path(..., min_length=1, max_length=64, pattern=HOUSE_ID_PATTERN)):
    # Переименование и есть перенос: калибровка лежит в записи корпуса и идёт с ней.
    # Занятое имя — 409, а не слияние: у того корпуса свои измерения (HouseIdTaken)
    s = load_settings()
    _house(s, house_id)
    if req.new_house_id == house_id:
        return get_settings(house_id)
    if req.new_house_id in s.houses:
        raise _taken(req.new_house_id)
    # Порядок корпусов сохраняется: первый корпус — корпус по умолчанию
    s.houses = {(req.new_house_id if k == house_id else k): v for k, v in s.houses.items()}
    save_settings(s)
    return get_settings(req.new_house_id)


@router.delete("/houses/{house_id}", dependencies=[Depends(require_write_token)])
def delete_house(house_id: str = Path(..., min_length=1, max_length=64, pattern=HOUSE_ID_PATTERN)):
    s = load_settings()
    _house(s, house_id)
    if len(s.houses) == 1:
        # Без корпуса главной нечего считать — последний не удаляется
        raise HTTPException(status_code=409, detail={"code": "last_house", "params": {"house_id": house_id}})
    del s.houses[house_id]
    save_settings(s)
    return {"deleted": house_id, "houses": list(s.houses)}


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
    return get_settings(None)
