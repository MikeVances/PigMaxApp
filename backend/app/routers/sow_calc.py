"""
sow_calc.py — помещения свиноматок (этап 5): холостые, супоросные, опоросник.

Опоросник — два климата: помещение держат по свиноматке, гнездо поросят греют
местно. Мощность обогрева гнезда (лампы, подогреваемый пол) — параметр:
источника с типовой мощностью у нас нет, а подставлять «типичные 150 Вт» значит
выдумать число. Это тепло всё равно остаётся в помещении — оно входит в баланс
как приход явного тепла и уменьшает потребность в отоплении помещения.
"""
from __future__ import annotations

from typing import Literal, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, model_validator

from ..core import sow_climate as SC
from ..core.building_thermal import calculate_heat_balance
from ..core.max_ventilation import max_ventilation
from ..core.min_ventilation import DEFAULT_CO2_LIMIT_PPM, DEFAULT_CO2_OUTDOOR_PPM, min_ventilation
from ..core.pig_heat import FloorFeed, Method, MethodNotApplicable
from ..core.pig_thermal import HEATER_TYPE_MAP
from ..core.psychro import pressure_at_altitude_pa

router = APIRouter()

# Перед опоросом свиноматка в опоросе ещё супоросна: для методик без стадии
# «до опороса» (CIGR, РД-АПК) — супоросная на 112-й день
PRE_FARROWING_PREGNANCY_DAY = 112.0


class SowSetpointsRequest(BaseModel):
    profile: str
    stage: SC.SowStage
    lactation_day: Optional[float] = Field(None, ge=-7, le=42)
    covered_nest: bool = False
    weaning_day: float = Field(SC.DEFAULT_WEANING_DAY, ge=14, le=42)

    @model_validator(mode="after")
    def _check(self):
        if self.profile not in SC.SOW_PROFILES:
            raise ValueError(f"unknown sow profile: {self.profile}")
        return self


def _setpoints(req: SowSetpointsRequest) -> SC.SowSetpoints:
    try:
        return SC.sow_setpoints(req.profile, req.stage, req.lactation_day, req.covered_nest, req.weaning_day)
    except ValueError as e:
        raise HTTPException(status_code=422, detail={"code": "invalid_parameters", "message": str(e)})


@router.get("/sow-profiles")
def list_sow_profiles():
    return [{"id": p.id, "name": p.name, "source_id": p.source_id, "setpoint_meaning": p.setpoint_meaning,
             "has_dry_gestation": p.dry_c is not None, "has_nest": p.nest is not None,
             "has_ventilation_norm": p.vent_lactation_min is not None}
            for p in SC.SOW_PROFILES.values()]


@router.post("/calc/sow-setpoints")
def calc_sow_setpoints(req: SowSetpointsRequest):
    sp = _setpoints(req)
    return {"profile": sp.profile_id, "stage": sp.stage, "room_setpoint_c": sp.room_setpoint_c,
            "setpoint_meaning": sp.setpoint_meaning, "design_room_temp_c": sp.design_room_temp_c,
            "nest_c": sp.nest_c, "min_vent_m3h": sp.min_vent_m3h, "max_vent_m3h": sp.max_vent_m3h,
            "rh_pct": sp.rh_pct, "sources": [sp.source_id], "assumptions": sp.assumptions,
            "warnings": sp.warnings}


class SowRoomRequest(SowSetpointsRequest):
    num_sows: int = Field(..., gt=0, le=10000)
    sow_weight_kg: float = Field(..., gt=80, le=400)
    pregnancy_day: Optional[float] = Field(None, ge=0, le=120)
    litter_mass_kg: Optional[float] = Field(None, ge=0, le=200)
    milk_kg_day: Optional[float] = Field(None, ge=0, le=20)
    method: Method = "stinn_xin_2014"
    floor_feed: FloorFeed = "dry_feed_dry_floor"
    t_outside_c: float = Field(..., ge=-50, le=50)
    rh_outside_pct: float = Field(..., ge=0, le=100)
    rh_max_pct: Optional[float] = Field(None, gt=30, le=95)
    co2_limit_ppm: float = Field(DEFAULT_CO2_LIMIT_PPM, ge=1000, le=5000)
    co2_outdoor_ppm: float = Field(DEFAULT_CO2_OUTDOOR_PPM, ge=300, le=600)
    norm: Literal["profile", "pic", "rd_apk_2012", "none"] = "profile"
    ua_w_per_k: float = Field(0.0, ge=0)
    heater_power_kw: float = Field(0.0, ge=0)
    heater_type: Literal["gas", "electric"] = "gas"
    creep_heat_w_per_pen: float = Field(0.0, ge=0, le=2000)
    altitude_m: float = Field(0.0, ge=-100, le=4000)

    @model_validator(mode="after")
    def _gestation(self):
        if self.stage == "gestation" and self.pregnancy_day is None:
            raise ValueError("pregnancy_day is required for stage 'gestation'")
        if self.stage == "lactation" and self.lactation_day is None:
            raise ValueError("lactation_day is required for stage 'lactation'")
        return self


def _group(req: SowRoomRequest) -> tuple[str, dict, list[dict]]:
    """Группа модели тепла и её параметры для стадии помещения."""
    notes: list[dict] = []
    if req.stage == "dry":
        return "dry_sow", {}, notes
    if req.stage == "gestation":
        return "gestating_sow", {"pregnancy_day": req.pregnancy_day}, notes
    if req.lactation_day < 0 and req.method != "stinn_xin_2014":
        notes.append({"code": "pre_farrowing_as_late_gestation", "params": {"pregnancy_day": PRE_FARROWING_PREGNANCY_DAY}})
        return "gestating_sow", {"pregnancy_day": PRE_FARROWING_PREGNANCY_DAY}, notes
    return "lactating_sow", {"lactation_day": req.lactation_day, "litter_mass_kg": req.litter_mass_kg,
                             "milk_kg_day": req.milk_kg_day}, notes


@router.post("/calc/sow-room")
def calc_sow_room(req: SowRoomRequest):
    """Помещение свиноматок: уставки, минимальная вентиляция, отопление, летний максимум."""
    sp = _setpoints(req)
    group, heat_kw, notes = _group(req)
    t_room = sp.design_room_temp_c
    rh_max = req.rh_max_pct or sp.rh_pct or 65.0
    p_pa = pressure_at_altitude_pa(req.altitude_m)
    assumptions = list(sp.assumptions) + notes
    if req.rh_max_pct is None and sp.rh_pct is None:
        assumptions.append({"code": "rh_max_default", "params": {"rh_pct": 65.0}})

    # Норма: из профиля (голландская таблица на свиноматку) или источник явно
    core_norm = "none" if req.norm in ("profile", "none") else req.norm
    try:
        mv = min_ventilation(group, req.sow_weight_kg, t_room, req.t_outside_c, req.rh_outside_pct,
                             rh_max_pct=rh_max, co2_limit_ppm=req.co2_limit_ppm,
                             co2_outdoor_ppm=req.co2_outdoor_ppm, norm=core_norm, altitude_m=req.altitude_m,
                             method=req.method, floor_feed=req.floor_feed, **heat_kw)
    except MethodNotApplicable as e:
        raise HTTPException(status_code=422, detail={"code": "method_not_applicable", "message": str(e)})
    except ValueError as e:
        raise HTTPException(status_code=422, detail={"code": "invalid_parameters", "message": str(e)})

    candidates = [("co2", mv.co2_m3h)]
    if mv.moisture_m3h is not None:
        candidates.append(("moisture", mv.moisture_m3h))
    norm_value, sources = mv.norm_m3h, list(mv.sources)
    if req.norm == "profile":
        norm_value = sp.min_vent_m3h
        if norm_value is not None:
            sources.append(sp.source_id)
        else:
            assumptions.append({"code": "profile_has_no_ventilation_norm"})
    if norm_value is not None:
        candidates.append(("norm", norm_value))
    basis, q_per_sow = max(candidates, key=lambda c: c[1])

    heat = mv.heat
    creep_w = req.creep_heat_w_per_pen * req.num_sows if req.stage == "lactation" else 0.0
    if req.stage == "lactation" and req.creep_heat_w_per_pen == 0:
        assumptions.append({"code": "creep_heat_not_counted"})
    hb = calculate_heat_balance(
        ua_coefficient=req.ua_w_per_k, q_min_m3h=q_per_sow * req.num_sows, t_inside_target=t_room,
        t_outside=req.t_outside_c, num_animals=req.num_sows, animal_weight_kg=req.sow_weight_kg,
        heater_power_kw=req.heater_power_kw, heater_type=HEATER_TYPE_MAP[req.heater_type], p_pa=p_pa,
        animals_sensible_w=heat.sensible_w * req.num_sows + creep_w,
        co2_limit_ppm=req.co2_limit_ppm, co2_outdoor_ppm=req.co2_outdoor_ppm)

    # Летний максимум на свиноматку: профиль, иначе PIC (опорос 650 cfm, супоросные 150)
    summer = sp.max_vent_m3h
    if summer is None:
        mx = max_ventilation(group, req.sow_weight_kg, req.num_sows, req.t_outside_c, norm="pic",
                             method=req.method, **heat_kw)
        summer = mx.norm_m3h_per_head
        sources.append("pic_2021_sow")

    return {
        "setpoints": {"room_setpoint_c": sp.room_setpoint_c, "setpoint_meaning": sp.setpoint_meaning,
                      "design_room_temp_c": t_room, "nest_c": sp.nest_c, "rh_max_pct": rh_max},
        "heat_per_sow": {"total_w": heat.total_w, "sensible_w": heat.sensible_w, "vapour_g_h": heat.vapour_g_h,
                         "co2_l_h": heat.co2_l_h, "method": heat.method, "group": group},
        "min_ventilation": {"per_sow_m3h": round(q_per_sow, 1), "total_m3h": round(q_per_sow * req.num_sows, 0),
                            "basis": basis, "co2_m3h": mv.co2_m3h, "moisture_m3h": mv.moisture_m3h,
                            "norm_m3h": norm_value, "humidity_uncontrollable": mv.humidity_uncontrollable},
        "heating": {"heater_required_kw": hb.q_heater_required_kw, "walls_kw": hb.q_walls_kw,
                    "ventilation_kw": hb.q_ventilation_kw, "animals_and_creep_kw": hb.q_animals_kw,
                    "creep_heat_kw": round(creep_w / 1000, 2), "duty_cycle": hb.duty_cycle if req.heater_power_kw else None,
                    "heater_covers_need": hb.heater_covers_need if req.heater_power_kw else None,
                    "dilution_air_m3h": hb.dilution_air_m3h, "status_code": hb.status_code},
        "summer_max_per_sow_m3h": summer,
        "sources": list(dict.fromkeys(sources)),
        "assumptions": assumptions + mv.assumptions,
        "warnings": sp.warnings + mv.warnings,
    }
